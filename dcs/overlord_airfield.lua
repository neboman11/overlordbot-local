--[[
Overlord airfield mission companion (mission scripting environment).

Load order in the mission editor (MISSION START triggers, "DO SCRIPT FILE"):
  1. optional: your copy of airfield-config.example.lua (defines OverlordAirfieldConfig)
  2. overlord_airfield.lua (this file)

Runs inside the normal sanitized mission environment: no sockets, files or
secrets.  The server hook (Scripts/Hooks/overlord_hook.lua, feature "airfields")
calls exactly one entry point about once a second,

  OverlordAirfield.bridge(epoch, inbound_json, radio_context_json)

and relays the returned JSON snapshot to the brain's /airfield/exchange route.
The third argument is normalized data that the hook read in its own private
environment from the installed terrain's native Radio.lua (theatre, native
airdrome id, frequency in Hz, modulation, ground/tower/approach roles, callsign
aliases).  This script never reads files; a context for another theatre is
rejected, and a missing context leaves every airfield without radios (with an
explicit error) instead of guessing frequencies.

Airfields: only native AIRDROME airbases from world.getAirbases (no ships,
FARPs or helipads), with their native ids, names, current coalition, position
and weather (wind 10 m above the field from atmosphere.getWind, altimeter from
atmosphere.getTemperatureAndPressure at sea level at the field).  Runways come
from Airbase.getRunways: centre, length, width and course (DCS reports the
negated grid course; it is converted to a true heading).  A runway label is
used only when it is a valid designator consistent with the native course;
reciprocal ends and L/R are derived, the two native records of one strip are
merged, and an unusable or contradictory label drops that runway with an error
(the optional config can correct labels, close runways and set an active
runway/pattern; it cannot add runways or radios).

Human aircraft: identity catalog, incarnations, watch list, lease and lifecycle
events work exactly like the carrier companion.  Only aircraft on the brain's
watch list are polled (position/velocity/heading/inAir); AI and unregistered
aircraft are never polled.  ground_airfield_id is the last native birth/land
place (takeoff clears it), otherwise the mission client slot's airdromeId, and
only for a watched aircraft on the ground with no such history, a position
inside exactly one airdrome's native runway footprint or parking spot.

DCS APIs used: env.mission, world.getAirbases, Airbase getID/getName/getDesc/
getCoalition/isExist/getPoint/getRunways/getParking, coalition.getPlayers, Unit
methods getID/getName/getPlayerName/getCallsign/getDesc/isExist/getLife/
getPoint/getVelocity/getPosition/inAir (watched humans only), coord.LOtoLL,
atmosphere.getWind/getTemperatureAndPressure, timer.getTime/getAbsTime,
world.addEventHandler, net.lua2json/json2lua.
]]

if OverlordAirfield and OverlordAirfield.stop then
  OverlordAirfield.stop()
end

OverlordAirfield = (function()
  local M = {}
  local MAX_INBOUND, MAX_CONTEXT, MAX_SNAPSHOT = 65536, 262144, 262144
  local MAX_WATCH, MAX_PLAYERS, MAX_EVENTS, MAX_TEXT, MAX_MEMORY = 64, 256, 32, 64, 512
  local MAX_FIELDS, MAX_RADIOS, MAX_RUNWAYS, MAX_ALIASES, MAX_CONTEXT_RADIOS = 256, 16, 16, 8, 4096
  local MIN_HZ, MAX_HZ = 1000000, 1000000000
  local PA_PER_INHG = 3386.389
  local LABEL_TOLERANCE, PARALLEL_TOLERANCE = 35, 5      -- degrees
  local PARKING_RADIUS, RUNWAY_MARGIN, FIELD_RADIUS = 30, 10, 20000   -- metres
  local ROLE_ORDER = { 'ground', 'tower', 'approach' }
  local ROLES = { ground = true, tower = true, approach = true }
  local OPPOSITE_SIDE = { L = 'R', R = 'L', C = 'C', [''] = '' }
  local AIRDROME = type(Airbase) == 'table' and type(Airbase.Category) == 'table'
    and Airbase.Category.AIRDROME or 0
  local EV = world.event
  local CONTACT = { [EV.S_EVENT_LAND or -1] = true, [EV.S_EVENT_RUNWAY_TOUCH or -1] = true }
  local LIFT = { [EV.S_EVENT_TAKEOFF or -1] = true, [EV.S_EVENT_RUNWAY_TAKEOFF or -1] = true }
  local TERMINAL = {
    [EV.S_EVENT_DEAD or -1] = 'dead', [EV.S_EVENT_CRASH or -1] = 'dead', [EV.S_EVENT_PILOT_DEAD or -1] = 'dead',
    [EV.S_EVENT_UNIT_LOST or -1] = 'dead', [EV.S_EVENT_EJECTION or -1] = 'eject',
    [EV.S_EVENT_PLAYER_LEAVE_UNIT or -1] = 'disconnect',
  }
  local BIRTH, ENTER = EV.S_EVENT_BIRTH, EV.S_EVENT_PLAYER_ENTER_UNIT
  CONTACT[-1], LIFT[-1], TERMINAL[-1] = nil, nil, nil

  local cfg = type(OverlordAirfieldConfig) == 'table' and OverlordAirfieldConfig or {}
  local mission = type(env.mission) == 'table' and env.mission or {}
  local lease = 15
  local errors = {}
  -- serial/events are never reset, so incarnations and event ids are not reused across epochs.
  local state = { epoch = nil, seq = 0, consumed = 0, last = nil, running = true, serial = 0, events = 0 }
  local catalog, actors, memory, memoryCount = {}, {}, {}, 0
  local context = { raw = false, byField = {}, errors = {} }

  local function fail(message)
    if #errors < 16 then errors[#errors + 1] = message end
    env.error('Overlord airfield: ' .. message)
  end

  -- Strict UTF-8 (no overlongs/surrogates) so identities pass through JSON unchanged.
  local function utf8(value)
    local i, n = 1, #value
    while i <= n do
      local c, size = value:byte(i), nil
      if c < 0x80 then size = 1
      elseif c >= 0xC2 and c <= 0xDF then size = 2
      elseif c >= 0xE0 and c <= 0xEF then size = 3
      elseif c >= 0xF0 and c <= 0xF4 then size = 4
      else return false end
      for k = i + 1, i + size - 1 do
        local b = value:byte(k)
        if not b or b < 0x80 or b > 0xBF then return false end
      end
      local b = value:byte(i + 1)
      if (c == 0xE0 and b < 0xA0) or (c == 0xED and b > 0x9F) or (c == 0xF0 and b < 0x90)
          or (c == 0xF4 and b > 0x8F) then
        return false
      end
      i = i + size
    end
    return true
  end

  -- Identity text: 1-64 bytes of valid UTF-8, no ASCII control characters, not blank.
  local function text(value)
    return type(value) == 'string' and #value >= 1 and #value <= MAX_TEXT and not value:find('[%z\1-\31\127]')
      and value:find('[^ ]') ~= nil and utf8(value)
  end

  -- Diagnostic-only ASCII label, so error strings can be cut anywhere and stay valid.
  local function label(value, size)
    local safe = tostring(value):gsub('[^A-Za-z0-9 %-%._]', '?')
    return safe:sub(1, size or 40)
  end

  local function modexText(value)
    return type(value) == 'string' and #value >= 1 and #value <= 8 and value:find('^[A-Za-z0-9]+$') ~= nil
  end

  local function number(value, lo, hi, integer)
    return type(value) == 'number' and value == value and value >= lo and value <= hi
      and (not integer or value == math.floor(value))
  end

  local function idText(value)
    if number(value, 1, 2147483647, true) then return string.format('%d', value) end
    return nil
  end

  local function validId(id)
    return type(id) == 'string' and #id >= 1 and #id <= 64 and not id:find('[^%w%._:%-]')
  end

  local function bounded(v, horizontal, low, high)
    if type(v) ~= 'table' or not number(v.x, -horizontal, horizontal) or not number(v.y, low, high)
        or not number(v.z, -horizontal, horizontal) then
      return nil
    end
    return { x = v.x, y = v.y, z = v.z }
  end

  -- Same physical bounds the brain enforces; out-of-range samples are dropped, never clamped.
  local function point3(v) return bounded(v, 1e7, -2000, 100000) end
  local function velocity3(v) return bounded(v, 2000, -2000, 2000) end
  local function unit3(v) return bounded(v, 1.5, -1.5, 1.5) end

  local function exists(object)
    if not object then return false end
    local ok, result = pcall(function() return object:isExist() and object:getLife() >= 1 end)
    return ok and result == true
  end

  local function sides(fn)
    for sideName, side in pairs(type(mission.coalition) == 'table' and mission.coalition or {}) do
      local countries = type(side) == 'table' and side.country
      for _, country in ipairs(type(countries) == 'table' and countries or {}) do
        if type(country) == 'table' then fn(sideName, country) end
      end
    end
  end

  local function groupsOf(country, category)
    local holder = country[category]
    local groups = type(holder) == 'table' and holder.group
    return type(groups) == 'table' and groups or {}
  end

  local function angle(a, b)
    local d = (a - b) % 360
    return d > 180 and 360 - d or d
  end

  local atan2 = math.atan2 or math.atan

  -- True bearing of a map-grid direction at a point: ellipsoid bearing to a point 1 km
  -- along it, so grid convergence never leaks into a TRUE value.
  local function trueBearing(point, dx, dz)
    local n = math.sqrt(dx * dx + dz * dz)
    if n < 1e-6 then return nil end
    local ok1, lat1, lon1 = pcall(coord.LOtoLL, point)
    local ok2, lat2, lon2 = pcall(coord.LOtoLL, { x = point.x + dx / n * 1000, y = point.y, z = point.z + dz / n * 1000 })
    if not (ok1 and ok2 and number(lat1, -90, 90) and number(lat2, -90, 90) and number(lon1, -360, 360)
            and number(lon2, -360, 360)) then
      return nil
    end
    local r = math.pi / 180
    local dlon = (lon2 - lon1) * r
    local y = math.sin(dlon) * math.cos(lat2 * r)
    local x = math.cos(lat1 * r) * math.sin(lat2 * r) - math.sin(lat1 * r) * math.cos(lat2 * r) * math.cos(dlon)
    return atan2(y, x) / r % 360
  end

  local function trueHeading(point, position)
    local forward = type(position) == 'table' and unit3(position.x)
    if not forward then return nil end
    return trueBearing(point, forward.x, forward.z)
  end

  local function latLon(point)
    local ok, lat, lon = pcall(coord.LOtoLL, point)
    if ok and number(lat, -90, 90) and number(lon, -180, 180) then return lat, lon end
    return nil
  end

  -- Native airdromes ------------------------------------------------------------------

  local fields, fieldById, fieldByName = {}, {}, {}

  local function categoryOf(airbase)
    local ok, desc = pcall(function() return airbase:getDesc() end)
    return ok and type(desc) == 'table' and desc.category or nil
  end

  do
    local ok, bases = pcall(world.getAirbases)
    for _, airbase in ipairs(ok and type(bases) == 'table' and bases or {}) do
      if categoryOf(airbase) == AIRDROME then
        local okId, rawId, name = pcall(function() return airbase:getID(), airbase:getName() end)
        local id = okId and idText(rawId)
        if not id or not text(name) then
          fail('airdrome ' .. label(okId and name or '?') .. ': no valid native id/name')
        elseif fieldById[id] or fieldByName[name] then
          fail(label(name) .. ': duplicate airdrome id/name')
        else
          local okPoint, point = pcall(function() return point3(airbase:getPoint()) end)
          local field = { id = id, name = name, airbase = airbase, runways = {}, geometry = {},
                          center = okPoint and point or nil, parking = nil }
          fields[#fields + 1] = field
          fieldById[id], fieldByName[name] = field, field
        end
      end
    end
    table.sort(fields, function(a, b) return tonumber(a.id) < tonumber(b.id) end)
    if #fields > MAX_FIELDS then
      fail('more than ' .. MAX_FIELDS .. ' airdromes; extra omitted')
      for i = #fields, MAX_FIELDS + 1, -1 do
        fieldById[fields[i].id], fieldByName[fields[i].name] = nil, nil
        fields[i] = nil
      end
    end
  end

  -- Human slots: modex/callsign and native airdrome spawn from the mission editor.
  local slots, badModex = {}, {}
  sides(function(_, country)
    for _, category in ipairs({ 'plane', 'helicopter' }) do
      for _, group in ipairs(groupsOf(country, category)) do
        if type(group) == 'table' then
          local route = type(group.route) == 'table' and group.route.points
          local first = type(route) == 'table' and route[1]
          local link = type(first) == 'table' and idText(first.airdromeId)
          for _, raw in ipairs(type(group.units) == 'table' and group.units or {}) do
            if type(raw) == 'table' and (raw.skill == 'Client' or raw.skill == 'Player') and text(raw.name) then
              local modex = raw.onboard_num
              if type(modex) == 'number' then modex = number(modex, 0, 99999999, true) and string.format('%d', modex) end
              if modex ~= nil and modex ~= '' and not modexText(modex) then
                badModex[#badModex + 1] = label(raw.name)   -- omitted, never invented
                modex = nil
              end
              local callsign = type(raw.callsign) == 'table' and raw.callsign.name or nil
              slots[raw.name] = { modex = modexText(modex) and modex or '', callsign = text(callsign) and callsign or nil,
                                  link = link and fieldById[link] and link or nil }
            end
          end
        end
      end
    end
  end)

  if #badModex > 0 then
    fail(#badModex .. ' slot modex omitted (not 1-8 letters/digits): ' .. table.concat(badModex, ', ', 1, math.min(#badModex, 3)))
  end

  -- Runway designators ---------------------------------------------------------------

  local function parseDesignator(value)
    if type(value) == 'number' then
      if number(value, 1, 36, true) then return value, '' end
      return nil
    end
    if type(value) ~= 'string' then return nil end
    local digits, side = value:upper():match('^(%d%d?)([LRC]?)$')
    local n = tonumber(digits)
    if not n or n < 1 or n > 36 then return nil end
    return n, side
  end

  local function designatorText(n, side) return string.format('%02d', n) .. side end
  local function reciprocal(n, side) return (n + 17) % 36 + 1, OPPOSITE_SIDE[side] end

  -- Operational configuration: runway labels/closures/active runway/pattern only ----------

  local overrides = {}
  for key in pairs(cfg) do
    if key ~= 'leaseSeconds' and key ~= 'airfields' then fail('config ' .. label(key) .. ' rejected') end
  end
  if cfg.leaseSeconds ~= nil then
    if number(cfg.leaseSeconds, 5, 60) then lease = cfg.leaseSeconds else fail('config leaseSeconds rejected') end
  end
  if cfg.airfields ~= nil and type(cfg.airfields) ~= 'table' then fail('config airfields rejected') end

  local function designatorList(value)
    if type(value) ~= 'table' then return nil end
    local count = 0
    for _ in pairs(value) do count = count + 1 end
    if count ~= #value or count > 2 * MAX_RUNWAYS then return nil end
    local out = {}
    for i, item in ipairs(value) do
      local n, side = parseDesignator(item)
      if not n or type(item) ~= 'string' then return nil end
      out[i] = designatorText(n, side)
    end
    return out
  end

  local function labelMap(value)
    if type(value) ~= 'table' then return nil end
    local out = {}
    for key, item in pairs(value) do
      local n, side = parseDesignator(item)
      local keyOk = type(key) == 'string' and (#key >= 1 and #key <= 8 or key:find('^native:%d%d?$') ~= nil)
      if not keyOk or type(item) ~= 'string' or not n then return nil end
      out[key] = designatorText(n, side)
    end
    return out
  end

  for name, raw in pairs(type(cfg.airfields) == 'table' and cfg.airfields or {}) do
    local field = type(name) == 'string' and fieldByName[name]
    if not field then
      fail('config unknown airfield ' .. label(name))
    elseif type(raw) ~= 'table' then
      fail('config ' .. label(name) .. ' rejected')
    else
      local values, ok = {}, true
      for key, value in pairs(raw) do
        local parsed
        if key == 'runwayNames' then parsed = labelMap(value)
        elseif key == 'closedRunways' then parsed = designatorList(value)
        elseif key == 'activeRunway' then
          local n, side = parseDesignator(value)
          parsed = type(value) == 'string' and n and designatorText(n, side) or nil
        elseif key == 'pattern' then parsed = (value == 'left' or value == 'right') and value or nil
        end
        if parsed then
          values[key] = parsed
        else
          ok = false
          fail('config ' .. label(name) .. '.' .. label(key) .. ' rejected')
        end
      end
      if ok then overrides[field.id] = values end
    end
  end

  -- Native runways -------------------------------------------------------------------------

  -- Canonical (lower-numbered) end of a strip: designator, true and grid heading of that end.
  local function canonical(n, side, heading, grid)
    if n > 18 then
      local rn, rs = reciprocal(n, side)
      return rn, rs, (heading + 180) % 360, (grid + 180) % 360
    end
    return n, side, heading, grid
  end

  local function nativeLabelKeys(value)
    local keys = {}
    if type(value) == 'string' or type(value) == 'number' then keys[#keys + 1] = tostring(value) end
    local n, side = parseDesignator(value)
    if n then keys[#keys + 1] = designatorText(n, side) end
    return keys
  end

  local function loadRunways(field)
    local override = overrides[field.id] or {}
    local names, used = override.runwayNames or {}, {}
    local ok, list = pcall(function() return field.airbase:getRunways() end)
    if not ok or type(list) ~= 'table' then
      fail(label(field.name) .. ': native runways unavailable')
      return
    end
    local strips = {}
    for index, raw in ipairs(list) do
      if index > 4 * MAX_RUNWAYS then
        fail(label(field.name) .. ': too many native runways; extra ignored')
        break
      end
      local where = label(field.name) .. ' runway ' .. index
      local point = type(raw) == 'table' and point3(raw.position)
      if not point or not number(raw.course, -10, 10) or not number(raw.length, 100, 20000)
          or not number(raw.width, 5, 500) then
        fail(where .. ': invalid native geometry')
      else
        -- DCS reports the course negated: grid heading = -course.
        local grid = (-math.deg(raw.course)) % 360
        local heading = trueBearing(point, math.cos(math.rad(grid)), math.sin(math.rad(grid)))
        -- "native:<index>" (position in getRunways) labels one strip unambiguously and wins over
        -- a key naming its native label, which several strips may share.
        local corrected
        for _, key in ipairs(nativeLabelKeys(raw.Name)) do
          if names[key] then corrected, used[key] = names[key], true end
        end
        local indexKey = 'native:' .. index
        if names[indexKey] then corrected, used[indexKey] = names[indexKey], true end
        local n, side = parseDesignator(corrected or raw.Name)
        local strip = heading and { index = index, point = point, length = raw.length, width = raw.width, grid = grid,
                                    course = heading }
        if strip then field.geometry[#field.geometry + 1] = strip end   -- physical footprint, label or not
        if not heading then
          fail(where .. ': true course unavailable')
        elseif not n then
          fail(where .. ': unusable runway label ' .. label(raw.Name, 12))
        else
          local endHeading, endGrid
          if angle(n * 10, heading) <= LABEL_TOLERANCE then
            endHeading, endGrid = heading, grid
          elseif angle(n * 10, heading + 180) <= LABEL_TOLERANCE then
            endHeading, endGrid = (heading + 180) % 360, (grid + 180) % 360
          end
          if not endHeading then
            fail(where .. ': label ' .. designatorText(n, side) .. ' inconsistent with native course')
          else
            strip.n, strip.side, strip.heading, strip.grid = canonical(n, side, endHeading, endGrid)
            strips[#strips + 1] = strip
          end
        end
      end
    end
    for key in pairs(names) do
      if not used[key] then fail('config ' .. label(field.name) .. '.runwayNames ' .. label(key, 8) .. ' unmatched') end
    end
    -- Both ends of one physical strip may be reported separately: merge, never double count.
    local kept = {}
    for _, strip in ipairs(strips) do
      local same
      for _, other in ipairs(kept) do
        local dx, dz = strip.point.x - other.point.x, strip.point.z - other.point.z
        local parallel = angle(strip.grid, other.grid) <= PARALLEL_TOLERANCE
          or angle(strip.grid, other.grid + 180) <= PARALLEL_TOLERANCE
        if parallel and math.sqrt(dx * dx + dz * dz) <= math.max(50, other.width) then
          same = other
          break
        end
      end
      if not same then
        kept[#kept + 1] = strip
      elseif same.n ~= strip.n or same.side ~= strip.side then
        same.bad = true
        fail(label(field.name) .. ': contradictory labels for one runway strip (native ' .. same.index .. ','
          .. strip.index .. ')')
      end
    end
    -- Two parallel strips with the same unsuffixed number get L/R from their native positions,
    -- but only when no strip of that number carries a suffix (labels must then be complete).
    local groups, suffixed = {}, {}
    for _, strip in ipairs(kept) do
      if not strip.bad then
        local key = designatorText(strip.n, strip.side)
        groups[key] = groups[key] or {}
        table.insert(groups[key], strip)
        if strip.side ~= '' then suffixed[strip.n] = true end
      end
    end
    for key, group in pairs(groups) do
      local a, b = group[1], group[2]
      if a.side == '' and suffixed[a.n] or #group > 1 then
        if #group == 2 and a.side == '' and not suffixed[a.n] and angle(a.grid, b.grid) <= PARALLEL_TOLERANCE then
          local r = math.rad(a.grid)
          local dx, dz = math.cos(r), math.sin(r)
          local leftOfA = (b.point.x - a.point.x) * dz - (b.point.z - a.point.z) * dx > 0
          a.side, b.side = leftOfA and 'R' or 'L', leftOfA and 'L' or 'R'
        else
          local indices = {}
          for _, strip in ipairs(group) do
            strip.bad = true
            indices[#indices + 1] = strip.index
          end
          table.sort(indices)
          fail(label(field.name) .. ': runways labelled ' .. key .. ' are ambiguous (native '
            .. table.concat(indices, ',') .. '); set runwayNames native:<index>')
        end
      end
    end
    -- Configured or derived L/R must match native positions: an L strip lies left of its R twin.
    for i, a in ipairs(kept) do
      for j = i + 1, #kept do
        local b = kept[j]
        local pair = a.side .. b.side
        if not a.bad and not b.bad and a.n == b.n and (pair == 'LR' or pair == 'RL')
            and angle(a.grid, b.grid) <= PARALLEL_TOLERANCE then
          local r = math.rad(a.grid)
          local dx, dz = math.cos(r), math.sin(r)
          local bLeft = (b.point.x - a.point.x) * dz - (b.point.z - a.point.z) * dx > 0
          if bLeft ~= (b.side == 'L') then
            a.bad, b.bad = true, true
            fail(label(field.name) .. ': L/R labels contradict native positions (native ' .. a.index .. ','
              .. b.index .. ')')
          end
        end
      end
    end
    -- Final guard: no two selectable strips may end up with the same designator.
    local byName = {}
    for _, strip in ipairs(kept) do
      if not strip.bad then
        local key = designatorText(strip.n, strip.side)
        byName[key] = byName[key] or {}
        table.insert(byName[key], strip)
      end
    end
    for key, group in pairs(byName) do
      if #group > 1 then
        local indices = {}
        for _, strip in ipairs(group) do
          strip.bad = true
          indices[#indices + 1] = strip.index
        end
        table.sort(indices)
        fail(label(field.name) .. ': runways labelled ' .. key .. ' collide (native ' .. table.concat(indices, ',') .. ')')
      end
    end
    local out = {}
    for _, strip in ipairs(kept) do
      if not strip.bad then
        local name = designatorText(strip.n, strip.side)
        local opposite = designatorText(reciprocal(strip.n, strip.side))
        out[#out + 1] = { id = name .. '-' .. opposite, name = name, opposite_name = opposite, point = strip.point,
                          heading_deg = strip.heading, length_m = strip.length, width_m = strip.width, closed = false,
                          geometry = strip }   -- internal; fieldRecord copies only wire fields
      end
    end
    table.sort(out, function(a, b) return a.id < b.id end)
    if #out > MAX_RUNWAYS then
      fail(label(field.name) .. ': more than ' .. MAX_RUNWAYS .. ' runways; extra omitted')
      for i = #out, MAX_RUNWAYS + 1, -1 do out[i] = nil end
    end
    local ends = {}
    for _, runway in ipairs(out) do ends[runway.name], ends[runway.opposite_name] = runway, runway end
    for _, closed in ipairs(override.closedRunways or {}) do
      if ends[closed] then
        ends[closed].closed = true
      else
        fail('config ' .. label(field.name) .. '.closedRunways ' .. closed .. ' unknown')
      end
    end
    if override.activeRunway then
      local runway = ends[override.activeRunway]
      if runway and not runway.closed then
        field.active = override.activeRunway
      else
        fail('config ' .. label(field.name) .. '.activeRunway ' .. override.activeRunway .. ' unknown or closed')
      end
    end
    field.pattern = override.pattern
    -- Every physical strip reaches the brain for occupancy: strips without a usable label go out
    -- as geometry only (id "native:<index>", empty name, true heading of the native course, no
    -- closed claim), once per physical strip so they never pair with a labelled runway's ends.
    local exported, omitted = {}, 0
    for _, runway in ipairs(out) do exported[#exported + 1] = runway.geometry end
    for _, strip in ipairs(field.geometry) do
      local represented = false
      for _, other in ipairs(exported) do
        local dx, dz = strip.point.x - other.point.x, strip.point.z - other.point.z
        local parallel = angle(strip.grid, other.grid) <= PARALLEL_TOLERANCE
          or angle(strip.grid, other.grid + 180) <= PARALLEL_TOLERANCE
        if parallel and math.sqrt(dx * dx + dz * dz) <= math.max(50, other.width) then
          represented = true
          break
        end
      end
      if not represented then
        exported[#exported + 1] = strip
        if #out >= MAX_RUNWAYS then
          omitted = omitted + 1
        else
          out[#out + 1] = { id = 'native:' .. strip.index, name = '', point = strip.point, heading_deg = strip.course,
                            length_m = strip.length, width_m = strip.width }
        end
      end
    end
    if omitted > 0 then
      fail(label(field.name) .. ': ' .. omitted .. ' unlabelled runway geometry omitted (over ' .. MAX_RUNWAYS .. ')')
    end
    field.runways = out
  end

  for _, field in ipairs(fields) do
    local ok = pcall(loadRunways, field)
    if not ok then
      field.runways = {}
      fail(label(field.name) .. ': runway data rejected')
    end
  end

  -- Native radio context from the hook ---------------------------------------------------

  local function roleList(value)
    if type(value) ~= 'table' then return nil end
    local seen, count = {}, 0
    for _, role in ipairs(value) do
      if not ROLES[role] then return nil end
      if not seen[role] then seen[role], count = true, count + 1 end
    end
    if count == 0 then return nil end
    return seen
  end

  local function aliasList(value)
    if value == nil then return {} end
    if type(value) ~= 'table' then return nil end
    local out = {}
    for i, alias in ipairs(value) do
      if i > MAX_ALIASES or not text(alias) then return nil end
      out[i] = alias
    end
    return out
  end

  -- Parse once per distinct context string; the result is the same for repeats.
  local function applyContext(raw)
    if raw == context.raw then return end
    context.raw, context.byField, context.errors = raw, {}, {}
    local function problem(message) context.errors[#context.errors + 1] = message end
    if type(raw) ~= 'string' or raw == '' then
      problem('native radio data unavailable (hook airfields context missing)')
      return
    end
    if #raw > MAX_CONTEXT then
      problem('native radio context too large')
      return
    end
    local ok, data = pcall(net.json2lua, raw)
    if not ok or type(data) ~= 'table' then
      problem('native radio context malformed')
      return
    end
    if data.error ~= nil then problem('native radio data: ' .. label(data.error, 120)) end
    if not text(mission.theatre) or data.theatre ~= mission.theatre then
      problem('native radio context theatre ' .. label(data.theatre) .. ' does not match mission theatre '
        .. label(mission.theatre))
      return
    end
    local list = data.radios
    if type(list) ~= 'table' or #list > MAX_CONTEXT_RADIOS then
      problem('native radio context list rejected')
      return
    end
    local grouped, rejected, unknown = {}, 0, 0
    for _, item in ipairs(list) do
      local roles = type(item) == 'table' and roleList(item.roles)
      local aliases = type(item) == 'table' and aliasList(item.aliases)
      if not (roles and aliases and validId(item.airfield_id) and number(item.frequency_hz, MIN_HZ, MAX_HZ - 1, true)
              and (item.modulation == 0 or item.modulation == 1)) then
        rejected = rejected + 1
      elseif not fieldById[item.airfield_id] then
        unknown = unknown + 1
      else
        local byKey = grouped[item.airfield_id] or {}
        grouped[item.airfield_id] = byKey
        local key = item.frequency_hz .. ':' .. item.modulation
        local entry = byKey[key]
        if not entry then
          entry = { frequency_hz = item.frequency_hz, modulation = item.modulation, roleSet = {}, aliases = {}, seen = {} }
          byKey[key] = entry
        end
        for role in pairs(roles) do entry.roleSet[role] = true end
        for _, alias in ipairs(aliases) do
          if not entry.seen[alias] and #entry.aliases < MAX_ALIASES then
            entry.seen[alias] = true
            entry.aliases[#entry.aliases + 1] = alias
          end
        end
      end
    end
    for id, byKey in pairs(grouped) do
      local radios = {}
      for _, entry in pairs(byKey) do
        local roles = {}
        for _, role in ipairs(ROLE_ORDER) do if entry.roleSet[role] then roles[#roles + 1] = role end end
        radios[#radios + 1] = { frequency_hz = entry.frequency_hz, modulation = entry.modulation, roles = roles,
                                aliases = entry.aliases }
      end
      table.sort(radios, function(a, b)
        if a.frequency_hz ~= b.frequency_hz then return a.frequency_hz < b.frequency_hz end
        return a.modulation < b.modulation
      end)
      if #radios > MAX_RADIOS then
        problem(label(fieldById[id].name) .. ': more than ' .. MAX_RADIOS .. ' native radios; extra omitted')
        for i = #radios, MAX_RADIOS + 1, -1 do radios[i] = nil end
      end
      context.byField[id] = radios
    end
    if rejected > 0 then problem(rejected .. ' native radio entries rejected') end
    if unknown > 0 then problem(unknown .. ' native radio entries for airdromes not in this mission') end
  end

  -- Airfield records -------------------------------------------------------------------

  local function altimeter(point)
    local ok, _, pressure = pcall(atmosphere.getTemperatureAndPressure, { x = point.x, y = 0, z = point.z })
    if not ok or not number(pressure, 50000, 120000) then return nil end
    local inhg = math.floor(pressure / PA_PER_INHG * 100 + 0.5) / 100
    return number(inhg, 25, 32.5) and inhg or nil
  end

  -- Wind 10 m above the field.  DCS gives the vector the air moves TOWARD; report FROM.
  -- Calm (< 0.05 m/s) has no direction: speed 0 and no direction, never an invented one.
  local function wind(point)
    local ok, v = pcall(atmosphere.getWind, { x = point.x, y = point.y + 10, z = point.z })
    v = ok and bounded(v, 200, -200, 200)
    if not v then return nil, nil end
    local speed = math.sqrt(v.x * v.x + v.z * v.z)
    if speed < 0.05 then return nil, 0 end
    local from = trueBearing(point, -v.x, -v.z)
    if not from then return nil, nil end
    return from, math.floor(speed * 100 + 0.5) / 100
  end

  local function fieldRecord(field, transient)
    local runways = {}
    for i, runway in ipairs(field.runways) do
      runways[i] = { id = runway.id, name = runway.name, opposite_name = runway.opposite_name, point = runway.point,
                     heading_deg = runway.heading_deg, length_m = runway.length_m, width_m = runway.width_m,
                     closed = runway.closed }
    end
    local out = { id = field.id, name = field.name, coalition = 0, alive = false,
                  radios = context.byField[field.id] or {}, runways = runways,
                  active_runway = field.active, pattern = field.pattern }
    local okSide, side = pcall(function() return field.airbase:getCoalition() end)
    if okSide and (side == 0 or side == 1 or side == 2) then
      out.coalition = side
    else
      transient[#transient + 1] = label(field.name) .. ': ownership unavailable'
    end
    local okLive, live = pcall(function() return field.airbase:isExist() end)
    if not okLive or live ~= true then return out end
    local okPoint, point = pcall(function() return point3(field.airbase:getPoint()) end)
    if not okPoint or not point then return out end
    local lat, lon = latLon(point)
    if not lat then return out end
    out.alive, out.point, out.lat, out.lon = true, point, lat, lon
    out.wind_from_deg, out.wind_speed_mps = wind(point)
    out.altimeter_inhg = altimeter(point)
    return out
  end

  -- Identity and lifecycle -----------------------------------------------------------

  local function unitIdOf(object)
    if not object then return nil end
    local ok, id = pcall(function() return object:getID() end)
    return ok and idText(id) or nil
  end

  local function playerOf(object)
    local ok, name = pcall(function() return object:getPlayerName() end)
    return ok and text(name) and name or nil
  end

  -- Native airdrome id of an event place; ships/FARPs (whose ids may collide) never match.
  local function placeField(place)
    if not place then return nil end
    local ok, category, rawId, name = pcall(function()
      local desc = place:getDesc()
      return type(desc) == 'table' and desc.category, place:getID(), place:getName()
    end)
    local id = ok and category == AIRDROME and idText(rawId)
    local field = id and fieldById[id]
    return field and field.name == name and id or nil
  end

  -- Last observed birth/land airdrome of a human unit (false: known not on an airdrome).
  local function setField(id, fieldId)
    if memory[id] == nil then
      memoryCount = memoryCount + 1
      if memoryCount > MAX_MEMORY then
        local kept = 0
        for key in pairs(memory) do
          if catalog[key] then kept = kept + 1 else memory[key] = nil end
        end
        memoryCount = kept + 1
      end
    end
    memory[id] = fieldId or false
  end

  local function push(actor, kind, fieldId)
    if #actor.events >= MAX_EVENTS then
      table.remove(actor.events, 1)
      actor.overflow = true
    end
    state.events = state.events + 1
    actor.events[#actor.events + 1] = { data = { id = state.events, type = kind, airfield_id = fieldId,
                                                 time = timer.getTime() } }
    if kind == 'land' or kind == 'takeoff' then actor.lastKind = kind end
  end

  local function watchedActor(id)
    local actor, entry = actors[id], catalog[id]
    if actor and entry and actor.incarnation == entry.incarnation and not entry.terminal then return actor end
    return nil
  end

  local function onEvent(event)
    if not state.running or type(event) ~= 'table' then return end
    if event.id ~= nil and event.id == EV.S_EVENT_MISSION_END then
      M.stop()
      return
    end
    local kind = event.id
    local contact, lift, terminal = CONTACT[kind], LIFT[kind], TERMINAL[kind]
    local birth, enter = kind ~= nil and kind == BIRTH, kind ~= nil and kind == ENTER
    if not (contact or lift or terminal or birth or enter) then return end
    local id = unitIdOf(event.initiator)
    if not id then return end
    if terminal then
      local entry = catalog[id]
      if not entry then return end
      local actor = watchedActor(id)
      if actor then
        push(actor, terminal, nil)
        entry.terminal = true
      else
        entry.dirty = true
      end
      return
    end
    if birth then
      -- Birth resets airdrome identity.  Mission client slots are reset even while
      -- getPlayerName is still nil, so a previous occupant's field never leaks into the
      -- new one; other units need a player (no AI state).
      local ok, name = pcall(function() return event.initiator:getName() end)
      local slot = ok and type(name) == 'string' and slots[name]
      local fieldId = placeField(event.place)
      if slot or playerOf(event.initiator) then setField(id, fieldId or (slot and slot.link)) end
      local actor = watchedActor(id)
      if actor then
        push(actor, 'birth', fieldId)      -- final event of the old incarnation
        catalog[id].terminal = true
      elseif catalog[id] then
        catalog[id].dirty = true
      end
      return
    end
    if enter then
      if catalog[id] then catalog[id].dirty = true end
      return
    end
    -- Land/takeoff are only recorded for human-occupied units (no AI state).
    if not playerOf(event.initiator) then return end
    local fieldId = placeField(event.place)
    setField(id, contact and fieldId or nil)
    local actor = watchedActor(id)
    if not actor then return end
    if contact and actor.lastKind ~= 'land' then
      push(actor, 'land', fieldId)
    elseif lift and actor.lastKind ~= 'takeoff' then
      push(actor, 'takeoff', fieldId)
    end
  end

  local function refreshPlayers(transient)
    local seen, count, overflow, rejected = {}, 0, false, false
    -- A watched identity that just ended stays listed for exactly one snapshot so its
    -- terminal event reaches the brain with a consistent identity.
    for id, entry in pairs(catalog) do
      if entry.terminal and not entry.terminalSent then
        seen[id] = entry
        count = count + 1
      end
    end
    for _, side in ipairs({ 1, 2 }) do
      local ok, units = pcall(coalition.getPlayers, side)
      for _, unit in ipairs(ok and type(units) == 'table' and units or {}) do
        local okUnit, rawId, name, player, desc = pcall(function()
          return unit:getID(), unit:getName(), unit:getPlayerName(), unit:getDesc()
        end)
        local id = okUnit and idText(rawId)
        local category = okUnit and type(desc) == 'table' and desc.category
        if id and not seen[id] and type(player) == 'string' and player ~= '' and (category == 0 or category == 1) then
          if not text(name) or not text(player) then
            rejected = true
          elseif count >= MAX_PLAYERS then
            overflow = true
          else
            local prior = catalog[id]
            local entry = prior
            if not (prior and not prior.dirty and not prior.terminal and prior.player_name == player
                    and prior.unit_name == name and prior.coalition == side) then
              state.serial = state.serial + 1
              entry = { unit_id = id, unit_name = name, player_name = player, coalition = side,
                        incarnation = id .. ':' .. state.serial }
            end
            local slot = slots[name]
            local okCall, callsign = pcall(function() return unit:getCallsign() end)
            entry.unit = unit
            entry.callsign = okCall and text(callsign) and callsign or slot and slot.callsign or ''
            entry.modex = slot and slot.modex or ''
            seen[id] = entry
            count = count + 1
          end
        end
      end
    end
    catalog = seen
    for id, actor in pairs(actors) do
      local entry = catalog[id]
      if not entry or entry.incarnation ~= actor.incarnation then actors[id] = nil end
    end
    if overflow then transient[#transient + 1] = 'more than ' .. MAX_PLAYERS .. ' human aircraft; extra omitted' end
    if rejected then
      transient[#transient + 1] = 'human identity omitted (blank/long/control/bad UTF-8)'
    end
  end

  -- Ground airfield membership -------------------------------------------------------

  local function parkingOf(field)
    if field.parking == nil then
      field.parking = {}
      local ok, spots = pcall(function() return field.airbase:getParking() end)
      for i, spot in ipairs(ok and type(spots) == 'table' and spots or {}) do
        if i > 2048 then break end
        local p = type(spot) == 'table' and point3(spot.vTerminalPos)
        if p then field.parking[#field.parking + 1] = p end
      end
    end
    return field.parking
  end

  local function insideStrip(strip, point)
    local r = math.rad(strip.grid)
    local dx, dz = math.cos(r), math.sin(r)
    local ox, oz = point.x - strip.point.x, point.z - strip.point.z
    local along, across = ox * dx + oz * dz, ox * dz - oz * dx
    return math.abs(along) <= strip.length / 2 + RUNWAY_MARGIN and math.abs(across) <= strip.width / 2 + RUNWAY_MARGIN
  end

  -- Inside exactly one airdrome's native runway footprint or parking spot; never "nearest".
  local function footprintField(point)
    local found
    for _, field in ipairs(fields) do
      local c = field.center
      if c and (point.x - c.x) ^ 2 + (point.z - c.z) ^ 2 <= FIELD_RADIUS * FIELD_RADIUS then
        local match = false
        for _, strip in ipairs(field.geometry) do
          if insideStrip(strip, point) then match = true break end
        end
        if not match then
          for _, p in ipairs(parkingOf(field)) do
            if (point.x - p.x) ^ 2 + (point.z - p.z) ^ 2 <= PARKING_RADIUS * PARKING_RADIUS then match = true break end
          end
        end
        if match then
          if found then return nil end
          found = field.id
        end
      end
    end
    return found
  end

  local function groundField(entry, point)
    local known = memory[entry.unit_id]
    if known ~= nil then return known or nil end
    local slot = slots[entry.unit_name]
    if slot and slot.link then return slot.link end
    return footprintField(point)
  end

  -- Watched aircraft polling ------------------------------------------------------------

  local function sample(actor)
    local entry = catalog[actor.unit_id]
    local unit = entry and entry.unit
    local ok, point, velocity, position, air = false
    if entry and not entry.terminal and exists(unit) then
      ok, point, velocity, position, air = pcall(function()
        return point3(unit:getPoint()), velocity3(unit:getVelocity()), unit:getPosition(), unit:inAir()
      end)
    end
    local heading = ok and point and velocity and (air == true or air == false) and trueHeading(point, position)
    local lat, lon
    if heading then lat, lon = latLon(point) end
    if not lat then
      -- No valid live sample: skip this record (the brain releases on absence).  Only an
      -- identity that just ended reuses its last real sample to carry the terminal event.
      return entry and entry.terminal and actor.sample or nil
    end
    actor.sample = { point = point, velocity = velocity, heading = heading, lat = lat, lon = lon, in_air = air,
                     ground = not air and groundField(entry, point) or nil }
    return actor.sample
  end

  local function newActor(entry, fieldId)
    local actor = { unit_id = entry.unit_id, incarnation = entry.incarnation, airfield_id = fieldId, events = {},
                    lastKind = nil, sample = nil }
    local ok, air = pcall(function() return entry.unit:inAir() end)
    if ok and air == true then actor.lastKind = 'takeoff' elseif ok and air == false then actor.lastKind = 'land' end
    return actor
  end

  local function applyWatch(list, transient)
    local watched, count = {}, 0
    for _, raw in ipairs(list) do
      local id = type(raw) == 'table' and raw.unit_id
      local entry = type(id) == 'string' and catalog[id]
      if entry and entry.incarnation == raw.incarnation and type(raw.airfield_id) == 'string'
          and fieldById[raw.airfield_id] and not watched[id] then
        if count >= MAX_WATCH then
          transient[#transient + 1] = 'watch list over ' .. MAX_WATCH .. ' aircraft; extra not tracked'
          break
        end
        local actor = actors[id]
        if not (actor and actor.incarnation == entry.incarnation) then
          actor = newActor(entry, raw.airfield_id)
        end
        actor.airfield_id = raw.airfield_id    -- same-incarnation re-binding keeps history
        watched[id] = actor
        count = count + 1
      end
    end
    actors = watched
  end

  local function acknowledge(seq)
    for _, actor in pairs(actors) do
      local kept = {}
      for _, event in ipairs(actor.events) do
        if not event.sent or event.sent > seq then kept[#kept + 1] = event end
      end
      actor.events = kept
    end
  end

  local function expire(now)
    if state.last == nil or now - state.last > lease then
      state.last = nil
      actors = {}
    end
  end

  -- Snapshot ----------------------------------------------------------------------------

  local function monthLength(year, month)
    if month == 2 then
      return (year % 4 == 0 and (year % 100 ~= 0 or year % 400 == 0)) and 29 or 28
    end
    return (month == 4 or month == 6 or month == 9 or month == 11) and 30 or 31
  end

  local function missionClock()
    local abs = timer.getAbsTime()
    local date = type(mission.date) == 'table' and mission.date or {}
    local year, month, day = date.Year, date.Month, date.Day
    if not number(abs, 0, 1e8) then return nil, nil end
    local days = math.floor(abs / 86400)
    abs = abs - days * 86400
    if not (number(year, 1900, 2200, true) and number(month, 1, 12, true)
            and number(day, 1, monthLength(year, month), true)) then
      return abs, nil
    end
    for _ = 1, math.min(days, 3660) do
      day = day + 1
      if day > monthLength(year, month) then
        day, month = 1, month + 1
        if month > 12 then month, year = 1, year + 1 end
      end
    end
    return abs, { year = year, month = month, day = day }
  end

  local function sortedIds(map)
    local ids = {}
    for id in pairs(map) do ids[#ids + 1] = id end
    table.sort(ids, function(a, b) return tonumber(a) < tonumber(b) end)
    return ids
  end

  local function diagnostics(transient)
    local messages = {}
    for _, message in ipairs(errors) do messages[#messages + 1] = message end
    for _, message in ipairs(context.errors) do messages[#messages + 1] = message end
    for _, message in ipairs(transient) do messages[#messages + 1] = message end
    if #messages > 0 then return table.concat(messages, '; '):sub(1, 240) end
    return nil
  end

  local function snapshot(transient)
    local now = timer.getTime()
    local abs, date = missionClock()
    if not date then transient[#transient + 1] = 'mission date unavailable in env.mission' end
    local out = { version = 1, epoch = state.epoch, seq = state.seq, time = now, abs_time = abs, date = date,
                  airfields = {}, players = {}, aircraft = {} }
    for _, field in ipairs(fields) do
      local ok, record = pcall(fieldRecord, field, transient)
      if ok then
        out.airfields[#out.airfields + 1] = record
      else
        transient[#transient + 1] = label(field.name) .. ': airfield record failed'
      end
    end
    for _, id in ipairs(sortedIds(catalog)) do
      local entry = catalog[id]
      out.players[#out.players + 1] = { unit_id = id, unit_name = entry.unit_name, player_name = entry.player_name,
                                        callsign = entry.callsign, modex = entry.modex, coalition = entry.coalition,
                                        incarnation = entry.incarnation, human = true }
    end
    local marked, ended = {}, {}
    for _, id in ipairs(sortedIds(actors)) do
      local actor, entry = actors[id], catalog[id]
      local current = sample(actor)
      if current then
        local events = {}
        for _, event in ipairs(actor.events) do
          if not event.sent then
            event.sent = state.seq
            marked[#marked + 1] = event
          end
          events[#events + 1] = event.data
        end
        out.aircraft[#out.aircraft + 1] = {
          unit_id = id, incarnation = actor.incarnation, airfield_id = actor.airfield_id,
          point = current.point, lat = current.lat, lon = current.lon, velocity = current.velocity,
          heading_deg = current.heading, in_air = current.in_air, ground_airfield_id = current.ground,
          events = events,
        }
      end
      if actor.overflow then
        transient[#transient + 1] = 'event buffer overflow for unit ' .. id
        actor.overflow = nil
      end
      if entry.terminal and not entry.terminalSent then ended[#ended + 1] = entry end
    end
    for id, entry in pairs(catalog) do
      if entry.terminal and not actors[id] and not entry.terminalSent then ended[#ended + 1] = entry end
    end
    out.error = diagnostics(transient)
    local encoded = net.lua2json(out)
    if #encoded > MAX_SNAPSHOT then
      -- Envelope limit: withhold everything atomically and keep events/identities for later.
      for _, event in ipairs(marked) do event.sent = nil end
      transient[#transient + 1] = 'snapshot over ' .. MAX_SNAPSHOT .. ' bytes; airfield data withheld'
      return net.lua2json({ version = 1, epoch = state.epoch, seq = state.seq, time = now, abs_time = abs, date = date,
                            airfields = {}, players = {}, aircraft = {}, error = diagnostics(transient) })
    end
    for _, entry in ipairs(ended) do entry.terminalSent = true end
    return encoded
  end

  -- Hook entry point -------------------------------------------------------------------

  -- The watch list and lease are applied only from an accepted brain response for this
  -- epoch that echoes the sequence of the snapshot issued last, once.  Anything else
  -- (empty, malformed, rejected, previous epoch, replayed) changes nothing, so an
  -- unreachable brain lets the watch list expire after leaseSeconds.
  function M.bridge(epoch, inbound, radioContext)
    if not state.running or not validId(epoch) then return nil end
    if epoch ~= state.epoch then
      state.epoch, state.seq, state.consumed, state.last = epoch, 0, 0, nil
      catalog, actors = {}, {}
    end
    local now, transient = timer.getTime(), {}
    applyContext(radioContext)
    expire(now)
    refreshPlayers(transient)
    if state.seq > 0 and state.consumed < state.seq and type(inbound) == 'string' and #inbound > 0
        and #inbound <= MAX_INBOUND then
      local ok, message = pcall(net.json2lua, inbound)
      if ok and type(message) == 'table' and message.version == 1 and message.accepted == true
          and message.epoch == state.epoch and message.seq == state.seq and type(message.watch) == 'table' then
        state.consumed, state.last = state.seq, now
        acknowledge(state.seq)
        applyWatch(message.watch, transient)
      end
    end
    state.seq = state.seq + 1
    return snapshot(transient)
  end

  function M.stop()
    state.running = false
    catalog, actors = {}, {}
  end

  world.addEventHandler({ onEvent = function(_, event) onEvent(event) end })
  env.info('Overlord airfield: ' .. #fields .. ' airdrome(s) in mission')
  return M
end)()
