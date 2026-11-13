--[[
Overlord carrier mission companion (mission scripting environment).

Load order in the mission editor (MISSION START triggers, "DO SCRIPT FILE"):
  1. optional: your copy of carrier-config.example.lua (defines OverlordCarrierConfig)
  2. overlord_carrier.lua (this file)

Runs inside the normal sanitized mission environment: no sockets, files or
secrets.  The server hook (Scripts/Hooks/overlord_hook.lua, feature "carriers")
calls exactly one entry point, OverlordCarrier.bridge(epoch, inbound_json),
about once a second and relays the returned JSON snapshot to the brain's
/carrier/exchange route.

Carrier registry: every carrier ship unit in env.mission (fixed list of DCS
carrier types, or a live unit with the native "Aircraft Carriers" attribute).
Its radio is the ship UNIT's own mission-editor frequency, in Hz, and optional
modulation (DCS default AM when omitted).  Nothing is allocated, converted or
duplicated: a missing or non-integer-Hz frequency is reported as unavailable
with an explicit error.  Group-level frequency fields (MHz) are never used.
Liveness, position, velocity and true heading come from the live unit.  CASE,
ceiling and visibility come only from the optional operational config;
altimeter from the config or atmosphere.getTemperatureAndPressure at sea
level; deck angle/height only from fixed type profiles.

Human aircraft: the identity catalog lists units returned by
coalition.getPlayers with a non-empty getPlayerName (no positions).  Each
occupancy gets an incarnation that changes on birth, player change, slot change,
death/ejection/leave, or disappearance.  Only aircraft on the brain's watch
list (accepted response for this epoch echoing the latest snapshot sequence)
are polled for position/velocity/inAir and collect land/takeoff/lifecycle
events.  AI and unregistered aircraft are never polled.  The watch list is
cleared when no valid brain response arrives for leaseSeconds of mission time.

DCS APIs used: env.mission, Unit.getByName, coalition.getPlayers, Unit methods
getID/getName/getPlayerName/getCallsign/getDesc/isExist/isActive/getLife/
getPoint/getVelocity/getPosition/inAir/hasAttribute, Airbase getName/getUnit
(event.place), coord.LOtoLL, atmosphere.getTemperatureAndPressure, timer,
world.addEventHandler, net.lua2json/json2lua.
]]

if OverlordCarrier and OverlordCarrier.stop then
  OverlordCarrier.stop()
end

OverlordCarrier = (function()
  local M = {}
  local MAX_INBOUND, MAX_WATCH, MAX_PLAYERS, MAX_EVENTS, MAX_TEXT, MAX_DECKS = 65536, 64, 256, 16, 64, 512
  local MIN_HZ, MAX_HZ = 1000000, 1000000000
  local TICK, DECK_CONFIRM, DECK_SPEED = 0.5, 3, 1.0
  local PA_PER_INHG = 3386.389
  local COALITION = { red = 1, blue = 2 }
  -- Fixed deck geometry (angled-deck offset to port in degrees, flight deck height in metres),
  -- from the public MOOSE AIRBOSS carrier parameters for the DCS models.
  local GEOMETRY = {
    nimitz = { angle = 9.1359, height = 20.1494 },
    vinson = { angle = 9.1359, height = 18.30 },
    forrestal = { angle = 9.1359, height = 20 },
    essex = { angle = 0, height = 19.27 },
    tarawa = { angle = 0, height = 21 },
    invincible = { angle = 0, height = 12 },
  }
  -- Native DCS carrier unit types -> geometry profile (false: carrier, geometry unknown).
  local CARRIER_TYPES = {
    CVN_71 = 'nimitz', CVN_72 = 'nimitz', CVN_73 = 'nimitz', CVN_75 = 'nimitz', Stennis = 'nimitz',
    VINSON = 'vinson', Forrestal = 'forrestal', Essex = 'essex', LHA_Tarawa = 'tarawa',
    hms_invincible = 'invincible', KUZNECOW = false, CV_1143_5 = false, ara_vdm = false,
  }
  local EV = world.event
  local CONTACT = { [EV.S_EVENT_LAND or -1] = true, [EV.S_EVENT_RUNWAY_TOUCH or -1] = true }
  local LIFT = { [EV.S_EVENT_TAKEOFF or -1] = true, [EV.S_EVENT_RUNWAY_TAKEOFF or -1] = true }
  local TERMINAL = {
    [EV.S_EVENT_DEAD or -1] = 'dead', [EV.S_EVENT_CRASH or -1] = 'dead', [EV.S_EVENT_PILOT_DEAD or -1] = 'dead',
    [EV.S_EVENT_UNIT_LOST or -1] = 'dead', [EV.S_EVENT_EJECTION or -1] = 'eject',
    [EV.S_EVENT_PLAYER_LEAVE_UNIT or -1] = 'disconnect',
  }
  local RENEW = { [EV.S_EVENT_BIRTH or -1] = true, [EV.S_EVENT_PLAYER_ENTER_UNIT or -1] = true }
  CONTACT[-1], LIFT[-1], TERMINAL[-1], RENEW[-1] = nil, nil, nil, nil

  local cfg = type(OverlordCarrierConfig) == 'table' and OverlordCarrierConfig or {}
  local mission = type(env.mission) == 'table' and env.mission or {}
  local lease = 15
  local errors = {}
  local state = { epoch = nil, seq = 0, consumed = 0, last = nil, running = true, serial = 0, events = 0 }
  local catalog, actors, decks, deckCount = {}, {}, {}, 0

  local function fail(message)
    if #errors < 16 then errors[#errors + 1] = message end
    env.error('Overlord carrier: ' .. message)
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
  -- Accepted values are sent unmodified; nothing is truncated or rewritten.
  local function text(value)
    return type(value) == 'string' and #value >= 1 and #value <= MAX_TEXT and not value:find('[%z\1-\31\127]')
      and value:find('[^ ]') ~= nil and utf8(value)
  end

  -- Diagnostic-only ASCII label, so error strings can be cut anywhere and stay valid.
  local function label(value)
    local safe = tostring(value):gsub('[^A-Za-z0-9 %-%._]', '?')
    return safe:sub(1, 40)
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

  -- Carrier registry from env.mission ------------------------------------------

  local carriers, carrierById, carrierByName = {}, {}, {}

  local function nativeCarrier(name)
    local ok, result = pcall(function()
      local unit = Unit.getByName(name)
      return unit ~= nil and unit:hasAttribute('Aircraft Carriers')
    end)
    return ok and result == true
  end

  local function addCarrier(raw, coalitionId, sideName)
    local id, name = idText(raw.unitId), raw.name
    if not id or not text(name) or not text(raw.type) then
      fail('carrier of type ' .. label(raw.type) .. ': no valid unitId/name/type')
      return
    end
    if not coalitionId then
      fail(label(name) .. ': coalition ' .. label(sideName) .. ' unsupported')
      return
    end
    if carrierById[id] or carrierByName[name] then
      fail(label(name) .. ': duplicate unit id/name')
      return
    end
    local frequency, modulation = raw.frequency, raw.modulation
    if modulation == nil then modulation = 0 end
    if modulation ~= 0 and modulation ~= 1 then
      fail(label(name) .. ': modulation not 0/1')
      frequency, modulation = nil, 0
    elseif frequency == nil then
      fail(label(name) .. ': no frequency')
    elseif not number(frequency, MIN_HZ, MAX_HZ - 1, true) then
      fail(label(name) .. ': frequency not integer Hz')
      frequency = nil
    end
    local carrier = { id = id, name = name, type = raw.type, coalition = coalitionId, frequency = frequency,
                      modulation = modulation, profile = CARRIER_TYPES[raw.type] or nil }
    carriers[#carriers + 1] = carrier
    carrierById[id], carrierByName[name] = carrier, carrier
  end

  sides(function(sideName, country)
    for _, group in ipairs(groupsOf(country, 'ship')) do
      local units = type(group) == 'table' and group.units
      for _, raw in ipairs(type(units) == 'table' and units or {}) do
        if type(raw) == 'table' and type(raw.type) == 'string' and type(raw.name) == 'string'
            and (CARRIER_TYPES[raw.type] ~= nil or nativeCarrier(raw.name)) then
          addCarrier(raw, COALITION[sideName], sideName)
        end
      end
    end
  end)
  table.sort(carriers, function(a, b) return tonumber(a.id) < tonumber(b.id) end)

  -- Human slots: modex/callsign and deck spawn link from the mission editor.
  local slots, badModex = {}, {}
  sides(function(_, country)
    for _, category in ipairs({ 'plane', 'helicopter' }) do
      for _, group in ipairs(groupsOf(country, category)) do
        if type(group) == 'table' then
          local route = type(group.route) == 'table' and group.route.points
          local first = type(route) == 'table' and route[1]
          local link = type(first) == 'table' and idText(first.linkUnit)
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
                                  link = link and carrierById[link] and link or nil }
            end
          end
        end
      end
    end
  end)

  if #badModex > 0 then
    fail(#badModex .. ' slot modex omitted (not 1-8 letters/digits): ' .. table.concat(badModex, ', ', 1, math.min(#badModex, 3)))
  end

  -- Operational configuration: CASE/weather/profile only ------------------------

  for key in pairs(cfg) do
    if key ~= 'leaseSeconds' and key ~= 'carriers' then fail('config ' .. label(key) .. ' rejected') end
  end
  if cfg.leaseSeconds ~= nil then
    if number(cfg.leaseSeconds, 5, 60) then lease = cfg.leaseSeconds else fail('config leaseSeconds rejected') end
  end
  if cfg.carriers ~= nil and type(cfg.carriers) ~= 'table' then fail('config carriers rejected') end
  for name, raw in pairs(type(cfg.carriers) == 'table' and cfg.carriers or {}) do
    local carrier = type(name) == 'string' and carrierByName[name]
    if not carrier then
      fail('config unknown carrier ' .. label(name))
    elseif type(raw) ~= 'table' then
      fail('config ' .. label(name) .. ' rejected')
    else
      local values, ok = {}, true
      for key, value in pairs(raw) do
        local valid = key == 'recoveryCase' and number(value, 1, 3, true)
          or key == 'altimeterInHg' and number(value, 25, 32.5)
          or key == 'ceilingFt' and number(value, 0, 60000)
          or key == 'visibilityNm' and number(value, 0, 100)
          or key == 'profile' and type(value) == 'string' and GEOMETRY[value] ~= nil
        if valid then
          values[key] = value
        else
          ok = false
          fail('config ' .. label(name) .. '.' .. label(key) .. ' rejected')
        end
      end
      if ok then
        carrier.case, carrier.altimeter = values.recoveryCase, values.altimeterInHg
        carrier.ceiling, carrier.visibility = values.ceilingFt, values.visibilityNm
        carrier.profile = values.profile or carrier.profile
      end
    end
  end

  -- Geometry ---------------------------------------------------------------------

  local atan2 = math.atan2 or math.atan

  -- True heading: bearing on the ellipsoid between the unit and a point 1 km ahead,
  -- so map-grid convergence does not leak into the TRUE value.
  local function trueHeading(point, position)
    local forward = type(position) == 'table' and unit3(position.x)
    if not forward then return nil end
    local n = math.sqrt(forward.x * forward.x + forward.z * forward.z)
    if n < 1e-6 then return nil end
    local lat1, lon1 = coord.LOtoLL(point)
    local lat2, lon2 = coord.LOtoLL({ x = point.x + forward.x / n * 1000, y = point.y, z = point.z + forward.z / n * 1000 })
    if not (number(lat1, -90, 90) and number(lat2, -90, 90) and number(lon1, -360, 360) and number(lon2, -360, 360)) then
      return nil
    end
    local r = math.pi / 180
    local dlon = (lon2 - lon1) * r
    local y = math.sin(dlon) * math.cos(lat2 * r)
    local x = math.cos(lat1 * r) * math.sin(lat2 * r) - math.sin(lat1 * r) * math.cos(lat2 * r) * math.cos(dlon)
    local heading = atan2(y, x) / r % 360
    return heading
  end

  local function carrierUnit(carrier)
    local ok, unit = pcall(Unit.getByName, carrier.name)
    if not ok or not exists(unit) then return nil end
    local okActive, active = pcall(function() return unit:isActive() end)
    if okActive and active == false then return nil end
    return unit
  end

  local function carrierVelocity(carrier)
    local unit = carrier and carrierUnit(carrier)
    if not unit then return nil end
    local ok, v = pcall(function() return unit:getVelocity() end)
    return ok and velocity3(v) or nil
  end

  local function altimeter(carrier, point)
    if carrier.altimeter then return carrier.altimeter end
    local ok, _, pressure = pcall(atmosphere.getTemperatureAndPressure, { x = point.x, y = 0, z = point.z })
    if not ok or not number(pressure, 50000, 120000) then return nil end
    local inhg = math.floor(pressure / PA_PER_INHG * 100 + 0.5) / 100
    return number(inhg, 25, 32.5) and inhg or nil
  end

  local function carrierRecord(carrier)
    local geometry = carrier.profile and GEOMETRY[carrier.profile]
    local out = { id = carrier.id, name = carrier.name, type = carrier.type, coalition = carrier.coalition,
                  alive = false, frequency_hz = carrier.frequency, modulation = carrier.modulation,
                  recovery_case = carrier.case, ceiling_ft = carrier.ceiling, visibility_nm = carrier.visibility,
                  deck_angle_deg = geometry and geometry.angle, deck_height_m = geometry and geometry.height }
    local unit = carrierUnit(carrier)
    if not unit then return out end
    local ok, point, velocity, position = pcall(function()
      return point3(unit:getPoint()), velocity3(unit:getVelocity()), unit:getPosition()
    end)
    if not ok or not point or not velocity then return out end
    local heading = trueHeading(point, position)
    local okLL, lat, lon = pcall(coord.LOtoLL, point)
    if not heading or not okLL or not number(lat, -90, 90) or not number(lon, -180, 180) then return out end
    out.alive, out.point, out.velocity, out.heading_deg, out.lat, out.lon = true, point, velocity, heading, lat, lon
    out.altimeter_inhg = altimeter(carrier, point)
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

  local function placeCarrier(place)
    if not place then return nil end
    local ok, name = pcall(function() return place:getName() end)
    if ok and type(name) == 'string' and carrierByName[name] then return carrierByName[name].id end
    ok, name = pcall(function()
      local unit = place:getUnit()
      return unit and unit:getName()
    end)
    if ok and type(name) == 'string' and carrierByName[name] then return carrierByName[name].id end
    return nil
  end

  -- Deck identity of a human unit: last observed birth/land place (false: not on a carrier),
  -- otherwise the mission slot's spawn link.  Never inferred from proximity.
  local function deckOf(id, unitName)
    local deck = decks[id]
    if deck ~= nil then return deck or nil end
    local slot = slots[unitName]
    return slot and slot.link or nil
  end

  local function setDeck(id, carrierId)
    if decks[id] == nil then
      deckCount = deckCount + 1
      if deckCount > MAX_DECKS then
        local kept = 0
        for key in pairs(decks) do
          if catalog[key] then kept = kept + 1 else decks[key] = nil end
        end
        deckCount = kept + 1
      end
    end
    decks[id] = carrierId or false
  end

  local function push(actor, kind, carrierId)
    if #actor.events >= MAX_EVENTS then
      table.remove(actor.events, 1)
      actor.overflow = true
    end
    state.events = state.events + 1
    actor.events[#actor.events + 1] = { data = { id = state.events, type = kind, carrier_id = carrierId,
                                                 time = timer.getTime() } }
    if kind == 'land' or kind == 'takeoff' then
      actor.lastKind, actor.deckSince, actor.onDeck = kind, nil, false
    end
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
    local contact, lift, renew, terminal = CONTACT[kind], LIFT[kind], RENEW[kind], TERMINAL[kind]
    if not (contact or lift or renew or terminal) then return end
    local id = unitIdOf(event.initiator)
    if not id then return end
    if terminal then
      local entry = catalog[id]
      if not entry then return end
      local actor = watchedActor(id)
      if actor then
        push(actor, terminal, actor.carrier_id)
        actor.onDeck, actor.deckSince = false, nil
        entry.terminal = true
      else
        entry.dirty = true
      end
      return
    end
    if renew then
      -- Birth is the deck-identity reset boundary.  Mission client slots are reset even when
      -- getPlayerName is still nil at birth, so a previous occupant's deck never hides the
      -- slot's spawn link; other units need a player (no AI state).
      if kind == EV.S_EVENT_BIRTH then
        local ok, name = pcall(function() return event.initiator:getName() end)
        local slot = ok and type(name) == 'string' and slots[name]
        if slot or playerOf(event.initiator) then
          setDeck(id, placeCarrier(event.place) or (slot and slot.link))
        end
      end
      if catalog[id] then catalog[id].dirty = true end
      return
    end
    -- Land/takeoff are only recorded for human-occupied units (no AI state).
    if not playerOf(event.initiator) then return end
    local carrierId = placeCarrier(event.place)
    setDeck(id, contact and carrierId or nil)
    local actor = watchedActor(id)
    if not actor then return end
    if contact and actor.lastKind ~= 'land' then
      push(actor, 'land', carrierId)
    elseif lift and actor.lastKind ~= 'takeoff' then
      push(actor, 'takeoff', carrierId)
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

  -- Watched aircraft polling ------------------------------------------------------------

  local function sample(actor, now)
    local entry = catalog[actor.unit_id]
    local unit = entry and entry.unit
    local ok, point, velocity, position, air = false
    if entry and not entry.terminal and exists(unit) then
      ok, point, velocity, position, air = pcall(function()
        return point3(unit:getPoint()), velocity3(unit:getVelocity()), unit:getPosition(), unit:inAir()
      end)
    end
    local heading = ok and point and velocity and trueHeading(point, position)
    if not heading then
      -- No valid live sample: skip this record (the brain releases on absence).  Only an
      -- identity that just ended reuses its last real sample to carry the terminal event.
      actor.deckSince, actor.onDeck = nil, false
      return entry and entry.terminal and actor.sample or nil
    end
    local carrier = carrierById[actor.carrier_id]
    local stopped = false
    if air == false and deckOf(actor.unit_id, entry.unit_name) == actor.carrier_id then
      local ship = carrierVelocity(carrier)
      if ship then
        local dx, dy, dz = velocity.x - ship.x, velocity.y - ship.y, velocity.z - ship.z
        stopped = math.sqrt(dx * dx + dy * dy + dz * dz) < DECK_SPEED
      end
    end
    if stopped then
      actor.deckSince = actor.deckSince or now
      actor.onDeck = now - actor.deckSince >= DECK_CONFIRM
    else
      actor.deckSince, actor.onDeck = nil, false
    end
    actor.sample = { point = point, velocity = velocity, heading = heading, in_air = air == true }
    return actor.sample
  end

  local function newActor(entry, carrierId)
    local actor = { unit_id = entry.unit_id, incarnation = entry.incarnation, carrier_id = carrierId, events = {},
                    lastKind = nil, deckSince = nil, onDeck = false, sample = nil }
    local ok, air = pcall(function() return entry.unit:inAir() end)
    if ok and air == true then actor.lastKind = 'takeoff' elseif ok and air == false then actor.lastKind = 'land' end
    return actor
  end

  local function applyWatch(list, transient)
    local watched, count = {}, 0
    for _, raw in ipairs(list) do
      local id = type(raw) == 'table' and raw.unit_id
      local entry = type(id) == 'string' and catalog[id]
      if entry and entry.incarnation == raw.incarnation and type(raw.carrier_id) == 'string'
          and carrierById[raw.carrier_id] and not watched[id] then
        if count >= MAX_WATCH then
          transient[#transient + 1] = 'watch list over ' .. MAX_WATCH .. ' aircraft; extra not tracked'
          break
        end
        local actor = actors[id]
        if not (actor and actor.incarnation == entry.incarnation and actor.carrier_id == raw.carrier_id) then
          actor = newActor(entry, raw.carrier_id)
        end
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

  local function tick(_, now)
    if not state.running then return nil end
    expire(now)
    for _, actor in pairs(actors) do sample(actor, now) end
    return now + TICK
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

  local function snapshot(transient)
    local now = timer.getTime()
    local abs, date = missionClock()
    if not date then transient[#transient + 1] = 'mission date unavailable in env.mission' end
    local out = { version = 1, epoch = state.epoch, seq = state.seq, time = now, abs_time = abs, date = date,
                  carriers = {}, players = {}, aircraft = {} }
    for _, carrier in ipairs(carriers) do out.carriers[#out.carriers + 1] = carrierRecord(carrier) end
    for _, id in ipairs(sortedIds(catalog)) do
      local entry = catalog[id]
      out.players[#out.players + 1] = { unit_id = id, unit_name = entry.unit_name, player_name = entry.player_name,
                                        callsign = entry.callsign, modex = entry.modex, coalition = entry.coalition,
                                        incarnation = entry.incarnation, human = true }
    end
    for _, id in ipairs(sortedIds(actors)) do
      local actor, entry = actors[id], catalog[id]
      local current = sample(actor, now)
      if current then
        local events = {}
        for _, event in ipairs(actor.events) do
          event.sent = event.sent or state.seq
          events[#events + 1] = event.data
        end
        out.aircraft[#out.aircraft + 1] = {
          unit_id = id, incarnation = actor.incarnation, carrier_id = actor.carrier_id,
          point = current.point, velocity = current.velocity, heading_deg = current.heading,
          in_air = current.in_air, on_deck = actor.onDeck == true, events = events,
        }
      end
      if actor.overflow then
        transient[#transient + 1] = 'event buffer overflow for unit ' .. id
        actor.overflow = nil
      end
      if entry.terminal then entry.terminalSent = true end
    end
    for id, entry in pairs(catalog) do
      if entry.terminal and not actors[id] then entry.terminalSent = true end
    end
    local messages = {}
    for _, message in ipairs(errors) do messages[#messages + 1] = message end
    for _, message in ipairs(transient) do messages[#messages + 1] = message end
    if #messages > 0 then out.error = table.concat(messages, '; '):sub(1, 240) end
    return out
  end

  -- Hook entry point -------------------------------------------------------------------

  -- The watch list and lease are applied only from an accepted brain response for this
  -- epoch that echoes the sequence of the snapshot issued last, once.  Anything else
  -- (empty, malformed, rejected, previous epoch, replayed) changes nothing, so an
  -- unreachable brain lets the watch list expire after leaseSeconds.
  function M.bridge(epoch, inbound)
    if not state.running or not validId(epoch) then return nil end
    if epoch ~= state.epoch then
      state.epoch, state.seq, state.consumed, state.last = epoch, 0, 0, nil
      catalog, actors = {}, {}
    end
    local now, transient = timer.getTime(), {}
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
    return net.lua2json(snapshot(transient))
  end

  function M.stop()
    state.running = false
    catalog, actors = {}, {}
  end

  world.addEventHandler({ onEvent = function(_, event) onEvent(event) end })
  timer.scheduleFunction(tick, nil, timer.getTime() + TICK)
  env.info('Overlord carrier: ' .. #carriers .. ' carrier(s) in mission')
  return M
end)()
