--[[
Offline harness for dcs/overlord_airfield.lua and the airfields feature of
dcs/overlord_hook.lua (including the private native Radio.lua loader and the
optional companion auto-load from Scripts/Overlord).

Runs the real airfield companion (plus the real carrier and JTAC companions for
the three-feature hook case) and the real hook in strict sandboxed Lua
environments with test doubles for the DCS mission API, net.dostring_in,
LuaSocket and the brain.  The Radio.lua files used here are small SYNTHETIC
files in the native format (radioId "airfield<N>_<k>", role list, callsign
common/nato aliases, frequency band -> {modulation, Hz}); no terrain data is
shipped.  It proves the native radio join, loader confinement, airfield
geometry/weather/ownership, identity/watch/lease handling and feature isolation
in plain Lua; it is NOT acceptance inside a real DCS mission (event timing,
native runway data quality, Radio.lua contents of each installed terrain and
hook permissions must be checked on a DCS server).

Usage (from the repository root):  luajit tests/airfield_harness.lua
]]

local ROOT = (arg and arg[0] or 'tests/airfield_harness.lua'):gsub('[^/\\]*$', '') .. '../'
local failures, passed = 0, 0

-- JSON (enough for the bridge payloads) ---------------------------------------

local encode
local function encodeString(s)
  return '"' .. s:gsub('[%c"\\]', function(c)
    local map = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
    return map[c] or string.format('\\u%04x', c:byte())
  end) .. '"'
end
function encode(v)
  local t = type(v)
  if t == 'nil' then return 'null' end
  if t == 'boolean' then return tostring(v) end
  if t == 'number' then
    if v == math.floor(v) and math.abs(v) < 1e15 then return string.format('%d', v) end
    return string.format('%.6f', v)
  end
  if t == 'string' then return encodeString(v) end
  if v[1] ~= nil then
    local parts = {}
    for i = 1, #v do parts[i] = encode(v[i]) end
    return '[' .. table.concat(parts, ',') .. ']'
  end
  local keys = {}
  for k in pairs(v) do keys[#keys + 1] = k end
  table.sort(keys)
  local parts = {}
  for _, k in ipairs(keys) do parts[#parts + 1] = encodeString(tostring(k)) .. ':' .. encode(v[k]) end
  return '{' .. table.concat(parts, ',') .. '}'   -- empty tables encode as {} like DCS may
end

local function decode(s)
  local i = 1
  local function ws() i = s:find('[^ \t\r\n]', i) or #s + 1 end
  local value
  local function str()
    local out = {}
    i = i + 1
    while true do
      local c = s:sub(i, i)
      if c == '' then error('unterminated string') end
      if c == '"' then i = i + 1; break end
      if c == '\\' then
        local n = s:sub(i + 1, i + 1)
        if n == 'u' then
          out[#out + 1] = string.char(tonumber(s:sub(i + 2, i + 5), 16) % 256); i = i + 6
        else
          out[#out + 1] = ({ n = '\n', r = '\r', t = '\t', b = '\b', f = '\f' })[n] or n; i = i + 2
        end
      else
        out[#out + 1] = c; i = i + 1
      end
    end
    return table.concat(out)
  end
  function value()
    ws()
    local c = s:sub(i, i)
    if c == '{' then
      local obj = {}
      i = i + 1; ws()
      if s:sub(i, i) == '}' then i = i + 1; return obj end
      while true do
        ws(); local k = str(); ws(); i = i + 1
        obj[k] = value(); ws()
        local d = s:sub(i, i); i = i + 1
        if d == '}' then return obj end
        if d ~= ',' then error('bad object') end
      end
    elseif c == '[' then
      local arr = {}
      i = i + 1; ws()
      if s:sub(i, i) == ']' then i = i + 1; return arr end
      while true do
        arr[#arr + 1] = value(); ws()
        local d = s:sub(i, i); i = i + 1
        if d == ']' then return arr end
        if d ~= ',' then error('bad array') end
      end
    elseif c == '"' then return str()
    elseif s:sub(i, i + 3) == 'true' then i = i + 4; return true
    elseif s:sub(i, i + 4) == 'false' then i = i + 5; return false
    elseif s:sub(i, i + 3) == 'null' then i = i + 4; return nil
    end
    local num = s:match('^-?%d+%.?%d*[eE]?[-+]?%d*', i)
    if not num then error('bad json at ' .. i) end
    i = i + #num
    return tonumber(num)
  end
  local result = value()
  ws()
  if i <= #s then error('trailing json') end
  return result
end

-- Helpers ------------------------------------------------------------------------

local function readFile(path)
  local f = assert(io.open(path, 'rb'))
  local s = f:read('*a')
  f:close()
  return s
end

local function writeFile(path, content)
  os.execute('mkdir -p "' .. path:gsub('[^/]*$', '') .. '"')
  local f = assert(io.open(path, 'wb'))
  f:write(content)
  f:close()
end

local function tempDir()
  local dir = os.tmpname()
  os.remove(dir)
  os.execute('mkdir -p "' .. dir .. '"')
  return dir
end

local function compile(source, name, env)
  if setfenv then
    local chunk = assert(loadstring(source, name))
    setfenv(chunk, env)
    return chunk
  end
  return assert(load(source, name, 't', env))
end

local function check(name, fn)
  local ok, err = pcall(fn)
  if ok then
    passed = passed + 1
    print('PASS ' .. name)
  else
    failures = failures + 1
    print('FAIL ' .. name .. ': ' .. tostring(err))
  end
end

local function eq(actual, expected, what)
  if actual ~= expected then
    error((what or 'value') .. ': expected ' .. tostring(expected) .. ', got ' .. tostring(actual), 2)
  end
end

local function near(actual, expected, tolerance, what)
  if type(actual) ~= 'number' or math.abs(actual - expected) > tolerance then
    error((what or 'value') .. ': expected ~' .. tostring(expected) .. ', got ' .. tostring(actual), 2)
  end
end

local function has(haystack, needle, what)
  if type(haystack) ~= 'string' or not haystack:find(needle, 1, true) then
    error((what or 'text') .. ': missing "' .. needle .. '" in ' .. tostring(haystack), 2)
  end
end

local function find(list, key, value)
  for _, item in ipairs(list or {}) do if item[key] == value then return item end end
end

local function count(list) local n = 0 for _ in pairs(list or {}) do n = n + 1 end return n end

-- DCS mission scripting doubles -------------------------------------------------

local EVENT = { S_EVENT_TAKEOFF = 3, S_EVENT_LAND = 4, S_EVENT_CRASH = 5, S_EVENT_EJECTION = 6, S_EVENT_DEAD = 8,
                S_EVENT_PILOT_DEAD = 9, S_EVENT_MISSION_END = 12, S_EVENT_BIRTH = 15,
                S_EVENT_PLAYER_ENTER_UNIT = 20, S_EVENT_PLAYER_LEAVE_UNIT = 21, S_EVENT_KILL = 28,
                S_EVENT_UNIT_LOST = 30, S_EVENT_RUNWAY_TAKEOFF = 54, S_EVENT_RUNWAY_TOUCH = 55 }

-- The map grid is rotated 3 degrees from true north, so grid->true conversion is observable.
local CONVERGENCE = math.rad(3)
local LEAD, WING, AI = 'Viper 1-1', 'Viper 1-2', 'AI Tanker'
local ALL_ROLES = { 'ground', 'tower', 'approach' }

local DEFAULT_RADIOS = {
  { airfield_id = '21', frequency_hz = 121000000, modulation = 0, roles = ALL_ROLES, aliases = { 'Ironwood' } },
  { airfield_id = '21', frequency_hz = 251000000, modulation = 0, roles = ALL_ROLES, aliases = { 'Ironwood' } },
  { airfield_id = '22', frequency_hz = 121000000, modulation = 0, roles = { 'tower' }, aliases = { 'Maple' } },
  { airfield_id = '22', frequency_hz = 121000000, modulation = 0, roles = { 'ground' }, aliases = { 'Maple Ground' } },
}

local function contextFor(theatre, radios, err)
  return encode({ theatre = theatre, radios = radios or DEFAULT_RADIOS, error = err })
end

local function runway(name, gridDeg, x, z, length, width)
  return { Name = name, course = -math.rad(gridDeg), position = { x = x, y = 100, z = z },
           length = length or 2400, width = width or 60 }
end

local DEFAULT_CONFIG = {
  leaseSeconds = 15,
  airfields = {
    Ironwood = { closedRunways = { '27' } },
    Maple = { activeRunway = '22R', pattern = 'right' },
    Badfield = { runwayNames = { XX = '09' } },
    Ghost = { pattern = 'left' },
    ['FARP Alpha'] = { pattern = 'left' },
  },
}

-- Minimal-noise mission: no rejected airdrome, no Badfield runway diagnostics, so a test's own
-- diagnostics are not cut by the 240-byte error bound.
local function quiet(W)
  W.badname.category = 2
  W.badfield.runways = {}
end

local function newMission(opts)
  opts = opts or {}
  local W = { now = 0, abs = 8 * 3600, units = {}, handlers = {}, errors = {}, forbidden = {}, polls = {},
              order = {}, bases = {}, parkingCalls = 0 }
  local Object = {}
  Object.__index = Object
  local function poll(self) W.polls[self.name] = (W.polls[self.name] or 0) + 1 end
  function Object:isExist() return self.alive end
  function Object:isActive() return true end
  function Object:getLife() return self.alive and 10 or 0 end
  function Object:getID() return self.id end
  function Object:getName() return self.name end
  function Object:getTypeName() return self.type end
  function Object:getCoalition() return self.coalition end
  function Object:getPlayerName() return self.player end
  function Object:getCallsign() return self.callsign end
  function Object:getDesc() return { category = self.category } end
  function Object:hasAttribute() return false end
  function Object:getPoint() poll(self); return { x = self.x, y = self.y, z = self.z } end
  function Object:getVelocity() poll(self); return { x = self.vx, y = 0, z = self.vz } end
  function Object:getPosition()
    poll(self)
    return { p = { x = self.x, y = self.y, z = self.z }, x = { x = math.cos(self.heading), y = 0, z = math.sin(self.heading) },
             y = { x = 0, y = 1, z = 0 }, z = { x = -math.sin(self.heading), y = 0, z = math.cos(self.heading) } }
  end
  function Object:inAir() poll(self); return self.air end
  function W.unit(spec)
    local u = setmetatable({ id = spec.id, name = spec.name, type = spec.type or 'F-16C_50', coalition = spec.coalition or 2,
                             category = spec.category or 0, x = spec.x or 0, y = spec.y or 100, z = spec.z or 0,
                             vx = spec.vx or 0, vz = spec.vz or 0, heading = spec.heading or 0, alive = true,
                             air = spec.air == true, player = spec.player, callsign = spec.callsign }, Object)
    W.units[spec.name] = u
    W.order[#W.order + 1] = u
    return u
  end

  local Base = {}
  Base.__index = Base
  function Base:getID() return self.id end
  function Base:getName() return self.name end
  -- Native Airbase:getCategory() is Object.Category.BASE (4) for every airbase; only
  -- getDesc().category distinguishes AIRDROME (0) from HELIPAD (1) and SHIP (2).
  function Base:getCategory() return 4 end
  function Base:getDesc() return { category = self.category } end
  function Base:getCoalition()
    if self.ownerError then error('owner unavailable') end
    return self.coalition
  end
  function Base:isExist() return self.alive end
  function Base:getPoint() return { x = self.x, y = self.y, z = self.z } end
  function Base:getRunways() return self.runways end
  function Base:getParking() W.parkingCalls = W.parkingCalls + 1; return self.parking end
  function W.base(spec)
    spec.alive = spec.alive ~= false
    spec.runways = spec.runways or {}
    spec.parking = spec.parking or {}
    local b = setmetatable(spec, Base)
    W.bases[#W.bases + 1] = b
    return b
  end
  -- A place handle as DCS passes it in events (Airbase object).
  function W.event(id, unit, place)
    for _, handler in ipairs(W.handlers) do handler:onEvent({ id = id, time = W.now, initiator = unit, place = place }) end
  end
  function W.advance(dt) W.now = W.now + dt end

  local s40, c40 = math.sin(math.rad(40)), math.cos(math.rad(40))
  W.ironwood = W.base({ id = 21, name = 'Ironwood', category = 0, coalition = 2, x = 0, y = 100, z = 0,
                        runways = { runway('09', 90, 0, 0), runway('27', 270, 0, 0) },
                        parking = { { Term_Index = 1, vTerminalPos = { x = 200, y = 100, z = 0 } } } })
  -- Two parallel strips both natively "4": B lies 300 m left of A's direction of travel.
  W.maple = W.base({ id = 22, name = 'Maple', category = 0, coalition = 1, x = 100000, y = 300, z = 0,
                     runways = { runway(4, 40, 100000, 0, 3000, 45),
                                 runway(4, 40, 100000 + 300 * s40, -300 * c40, 3000, 45) },
                     parking = { { Term_Index = 7, vTerminalPos = { x = 100500, y = 300, z = 500 } } } })
  -- Labels: XX (corrected by config), ZZ unusable, 18 contradicts its course.  The parking spot
  -- lies on Ironwood's runway, so that spot proves nothing about either field.
  W.badfield = W.base({ id = 23, name = 'Badfield', category = 0, coalition = 0, x = 0, y = 100, z = 3000,
                        runways = { runway('XX', 90, 2000, 3000), runway('ZZ', 90, 2500, 3000),
                                    runway('18', 90, 3000, 3000) },
                        parking = { { Term_Index = 1, vTerminalPos = { x = 0, y = 100, z = 1000 } } } })
  W.ship = W.base({ id = 21, name = 'CVN Ship', category = 2, coalition = 2, x = -9000, y = 0, z = 0 })   -- id collides
  W.farp = W.base({ id = 50, name = 'FARP Alpha', category = 1, coalition = 2, x = 3000, y = 100, z = 0 })
  W.badname = W.base({ id = 24, name = 'Bad\1Name', category = 0, coalition = 2, x = 50000, y = 100, z = 0 })

  W.lead = W.unit({ id = 301, name = LEAD, x = 0, z = -500, player = 'Maverick', callsign = 'Enfield11' })
  W.wing = W.unit({ id = 302, name = WING, x = 5000, z = 5000, player = 'Iceman' })
  W.ai = W.unit({ id = 401, name = AI, type = 'KC135', x = 200, z = 0 })

  W.mission = {
    theatre = 'Caucasus',
    date = { Year = 2024, Month = 6, Day = 1 },
    coalition = {
      blue = { country = { {
        id = 2, name = 'USA',
        plane = { group = {
          { name = 'Viper 1', route = { points = { { type = 'TakeOffParking', airdromeId = 21 } } },
            units = { { unitId = 301, name = LEAD, skill = 'Client', onboard_num = '011',
                        callsign = { 1, 1, 1, name = 'Enfield11' } } } },
          { name = 'Viper 2', route = { points = { { type = 'Turning Point' } } },
            units = { { unitId = 302, name = WING, skill = 'Client', onboard_num = '012' } } },
          { name = 'Tanker', route = { points = { { type = 'TakeOffParking', airdromeId = 21 } } },
            units = { { unitId = 401, name = AI, skill = 'High', onboard_num = '900' } } },
        } },
      } } },
    },
  }

  W.wind = function(p)
    if p.x > 90000 then return { x = 0, y = 0, z = 0 } end      -- calm at Maple
    return { x = -5, y = 0, z = 0 }                              -- blowing to grid south
  end

  local allowedNil = { OverlordAirfield = true, OverlordAirfieldConfig = true, OverlordCarrier = true,
                       OverlordCarrierConfig = true, OverlordJTAC = true, OverlordJTACConfig = true }
  local base = {
    math = math, string = string, table = table, ipairs = ipairs, pairs = pairs, type = type,
    tostring = tostring, tonumber = tonumber, pcall = pcall, setmetatable = setmetatable, next = next,
    select = select, error = error,
    env = { mission = W.mission, info = function() end, error = function(m) W.errors[#W.errors + 1] = m end },
    timer = {
      getTime = function() return W.now end,
      getAbsTime = function() return W.abs end,
      scheduleFunction = function() end,
    },
    Unit = { getByName = function(n) return W.units[n] end },
    Group = { getByName = function() return nil end },
    StaticObject = { getByName = function() return nil end },
    Airbase = { Category = { AIRDROME = 0, HELIPAD = 1, SHIP = 2 } },
    Spot = {},
    land = { isVisible = function() return true end, getHeight = function() return 0 end },
    trigger = { action = {} },
    coalition = { getPlayers = function(side)
      local out = {}
      for _, u in ipairs(W.order) do
        if u.alive and u.player and u.coalition == side then out[#out + 1] = u end
      end
      return out
    end },
    coord = { LOtoLL = function(p)
      local north = p.x * math.cos(CONVERGENCE) - p.z * math.sin(CONVERGENCE)
      local east = p.x * math.sin(CONVERGENCE) + p.z * math.cos(CONVERGENCE)
      return 42 + north / 111000, 41 + east / (111000 * math.cos(math.rad(42))), p.y
    end },
    atmosphere = {
      getTemperatureAndPressure = function(p)
        assert(p.y == 0, 'sea-level pressure query')
        return 288.15, 101591.67
      end,
      getWind = function(p) return W.wind(p) end,
    },
    world = { event = EVENT, addEventHandler = function(h) W.handlers[#W.handlers + 1] = h end,
              getAirbases = function() return W.bases end },
    net = { lua2json = encode, json2lua = decode },
  }
  W.env = setmetatable(base, { __index = function(_, k)
    if allowedNil[k] then return nil end
    W.forbidden[#W.forbidden + 1] = k
    error('mission script touched forbidden global ' .. tostring(k), 2)
  end })
  if opts.config ~= false then W.env.OverlordAirfieldConfig = opts.config or DEFAULT_CONFIG end
  if opts.mutate then opts.mutate(W) end
  if opts.bare then
    -- A mission without companion triggers: the sanitized mission environment keeps loadstring,
    -- which binds compiled chunks to the mission globals.
    W.loads = {}
    rawset(W.env, 'loadstring', function(source, name)
      W.loads[#W.loads + 1] = { source = source, name = name }
      local ok, chunk = pcall(compile, source, name, W.env)
      return ok and chunk or nil
    end)
    W.context = contextFor('Caucasus')
    return W
  end
  if opts.others then
    W.env.OverlordJTACConfig = { tasks = {} }
    compile(readFile(ROOT .. 'dcs/overlord_jtac.lua'), '@overlord_jtac.lua', W.env)()
    compile(readFile(ROOT .. 'dcs/overlord_carrier.lua'), '@overlord_carrier.lua', W.env)()
  end
  compile(readFile(ROOT .. 'dcs/overlord_airfield.lua'), '@overlord_airfield.lua', W.env)()
  W.context = contextFor('Caucasus')
  return W
end

local function bridge(W, epoch, inbound, context)
  local raw = inbound == nil and '' or type(inbound) == 'string' and inbound or encode(inbound)
  if context == nil then context = W.context end
  local out = W.env.OverlordAirfield.bridge(epoch, raw, context or nil)
  W.last = out and decode(out)
  return W.last
end

local function accept(W, watch, overrides)
  local message = { version = 1, accepted = true, epoch = W.last.epoch, seq = W.last.seq, watch = watch or {} }
  for k, v in pairs(overrides or {}) do message[k] = v end
  return message
end

local function player(s, id) return find(s.players, 'unit_id', id) end
local function aircraft(s, id) return find(s.aircraft, 'unit_id', id) end
local function field(s, id) return find(s.airfields, 'id', id) end
-- Runway list as names, geometry-only strips (empty name) by their native id.
local function names(runways)
  local out = {}
  for _, r in ipairs(runways or {}) do out[#out + 1] = r.name ~= '' and r.name or r.id end
  return table.concat(out, ',')
end

local function watchOf(s, id, fieldId)
  return { unit_id = id, incarnation = player(s, id).incarnation, airfield_id = fieldId }
end

local function kinds(record)
  local out = {}
  for _, event in ipairs(record and record.events or {}) do
    out[#out + 1] = event.type .. (event.airfield_id and ('@' .. event.airfield_id) or '')
  end
  return table.concat(out, ',')
end

local function enrol(W, epoch, id, fieldId)
  local s = W.last and W.last.epoch == epoch and W.last or bridge(W, epoch)
  return bridge(W, epoch, accept(W, { watchOf(s, id, fieldId) }))
end

-- Mission companion ---------------------------------------------------------------

check('mission script has no file, OS, loader or debug access, statically and at run time', function()
  local source = readFile(ROOT .. 'dcs/overlord_airfield.lua')
  source = source:gsub('%-%-%[%[.-%]%]', ''):gsub('%-%-[^\n]*', '')
  for _, name in ipairs({ 'io', 'os', 'lfs', 'dofile', 'loadfile', 'loadstring', 'load', 'require', 'debug',
                          'package', 'socket', 'rawset', 'setfenv', 'getfenv' }) do
    assert(not source:find('%f[%w_]' .. name .. '%f[^%w_]'), 'mission script references ' .. name)
  end
  local W = newMission()
  local s = bridge(W, 'e1')
  W.event(EVENT.S_EVENT_BIRTH, W.lead, W.ironwood)
  bridge(W, 'e1', accept(W, { watchOf(s, '301', '21') }))
  eq(#W.forbidden, 0, 'forbidden globals touched: ' .. table.concat(W.forbidden, ','))
end)

check('only native airdromes are listed, by native id; ships, FARPs and bad records excluded per record', function()
  local W = newMission()
  local s = bridge(W, 'e1')
  local ids = {}
  for _, f in ipairs(s.airfields) do ids[#ids + 1] = f.id end
  eq(table.concat(ids, ','), '21,22,23', 'airdromes in id order; ship id collision, FARP and bad name excluded')
  eq(field(s, '21').name, 'Ironwood')
  eq(field(s, '22').coalition, 1)
  eq(field(s, '23').coalition, 0, 'neutral ownership reported, not hidden')
  has(s.error, 'airdrome Bad?Name: no valid native id/name')
  has(s.error, 'config unknown airfield Ghost', 'config cannot add airfields')
  has(s.error, 'config unknown airfield FARP Alpha', 'FARPs are not airfields')
  assert(not s.error:find('[\128-\255%c]'), 'diagnostics are printable ASCII')
  assert(#s.error <= 240)
  eq(#W.forbidden, 0)
end)

check('native runway course is negated grid, converted to true; ends merged; parallels get L/R', function()
  local W = newMission()
  local s = bridge(W, 'e1')
  local iron = field(s, '21')
  eq(count(iron.runways), 1, 'both native ends of one strip merged')
  local r = iron.runways[1]
  eq(r.id, '09-27')
  eq(r.name, '09')
  eq(r.opposite_name, '27')
  near(r.heading_deg, 93, 0.5, 'true heading of the 09 end (grid 90 + 3 convergence)')
  eq(r.length_m, 2400)
  eq(r.width_m, 60)
  eq(r.point.x, 0)
  eq(r.point.y, 100, 'native centre elevation kept')
  eq(r.closed, true, 'closing either end closes the strip')
  local maple = field(s, '22')
  eq(count(maple.runways), 2)
  local left, right = find(maple.runways, 'name', '04L'), find(maple.runways, 'name', '04R')
  assert(left and right, 'L/R derived from native positions')
  eq(left.opposite_name, '22R', 'reciprocal swaps L/R')
  eq(right.id, '04R-22L')
  near(left.point.x, 100000 + 300 * math.sin(math.rad(40)), 0.01, 'left strip is the offset one')
  eq(maple.active_runway, '22R', 'configured active runway end')
  eq(maple.pattern, 'right')
  eq(iron.active_runway, nil, 'no active runway invented')
  eq(iron.pattern, nil, 'no pattern invented')
  local bad = field(s, '23')
  eq(names(bad.runways), '09,native:2,native:3', 'corrected label usable; rejected strips stay as geometry')
  eq(bad.runways[1].point.x, 2000, 'corrected label keeps native geometry')
  local unknown = find(bad.runways, 'id', 'native:2')
  eq(unknown.name, '', 'no invented name')
  eq(unknown.opposite_name, nil)
  eq(unknown.closed, nil, 'no closed claim')
  eq(unknown.point.x, 2500, 'raw native centre')
  eq(unknown.length_m, 2400)
  eq(unknown.width_m, 60)
  near(unknown.heading_deg, 93, 0.5, 'true heading of the native course')
  has(s.error, 'Badfield runway 2: unusable runway label ZZ')
  has(s.error, 'Badfield runway 3: label 18 inconsistent with native course')
end)

check('runway configuration cannot contradict native geometry or add radios', function()
  local W = newMission({ mutate = quiet, config = {
    airfields = {
      Ironwood = { runwayNames = { ['09'] = '18' }, frequency = 121500000 },
      Maple = { runwayNames = { ['04'] = '31' }, activeRunway = '13', closedRunways = { '99' } },
    },
    owner = 'me',
  } })
  local s = bridge(W, 'e1')
  has(s.error, 'config owner rejected')
  has(s.error, 'config Ironwood.frequency rejected')
  has(s.error, 'config Maple.closedRunways rejected')
  eq(count(field(s, '21').runways), 1, 'rejected config leaves native labels in force')
  eq(field(s, '21').radios[1].frequency_hz, 121000000, 'radio only from the native context')
  W = newMission({ mutate = quiet, config = { airfields = { Maple = { runwayNames = { ['04'] = '31' } } } } })
  s = bridge(W, 'e1')
  eq(names(field(s, '22').runways), 'native:1,native:2', 'inconsistent labels leave geometry only')
  has(s.error, 'label 31 inconsistent with native course')
  W = newMission({ mutate = quiet, config = { airfields = { Maple = { activeRunway = '13' } } } })
  s = bridge(W, 'e1')
  eq(field(s, '22').active_runway, nil)
  has(s.error, 'activeRunway 13 unknown or closed')
end)

-- Three parallel strips DCS reports as "4": native 1 is the leftmost (in the 04 direction),
-- native 2 the centre, native 3 the rightmost.
local function triplet(W)
  quiet(W)
  local left = { x = math.sin(math.rad(40)), z = -math.cos(math.rad(40)) }
  local strips = {}
  for i, offset in ipairs({ 600, 300, 0 }) do
    strips[i] = runway(4, 40, 200000 + offset * left.x, offset * left.z, 3000, 45)
  end
  W.triplet = W.base({ id = 25, name = 'Triplet', category = 0, coalition = 2, x = 200000, y = 100, z = 0,
                       runways = strips })
end

check('three parallel strips need explicit native-index labels; L/R must match native positions', function()
  local W = newMission({ mutate = triplet, config = false })
  local s = bridge(W, 'e1')
  eq(names(field(s, '25').runways), 'native:1,native:2,native:3', 'three same-number strips are never guessed')
  has(s.error, 'Triplet: runways labelled 04 are ambiguous (native 1,2,3); set runwayNames native:<index>')
  W = newMission({ mutate = triplet, config = { airfields = { Triplet = {
    runwayNames = { ['4'] = '04L', ['native:2'] = '04C', ['native:3'] = '04R' } } } } })
  s = bridge(W, 'e1')
  local runways = field(s, '25').runways
  local ids = {}
  for _, r in ipairs(runways) do ids[#ids + 1] = r.id end
  eq(table.concat(ids, ','), '04C-22C,04L-22R,04R-22L', 'native index keys win over the shared label key')
  local c = 300 * math.sin(math.rad(40))
  near(find(runways, 'name', '04L').point.x, 200000 + 2 * c, 0.01, 'geometry stays native')
  near(find(runways, 'name', '04C').point.x, 200000 + c, 0.01)
  eq(find(runways, 'name', '04R').point.x, 200000)
  near(find(runways, 'name', '04R').heading_deg, 43, 2, 'true heading of the 04 end')
  eq(s.error, nil, 'no diagnostics for a consistent configuration')
  W = newMission({ mutate = triplet, config = { airfields = { Triplet = {
    runwayNames = { ['native:1'] = '04R', ['native:2'] = '04C', ['native:3'] = '04L', ['native:9'] = '04L' } } } } })
  s = bridge(W, 'e1')
  eq(names(field(s, '25').runways), '04C,native:1,native:3', 'swapped L/R rejected; strips stay as geometry')
  eq(field(s, '25').runways[1].name, '04C')
  has(s.error, 'Triplet: L/R labels contradict native positions (native 1,3)')
  W = newMission({ mutate = triplet, config = { airfields = { Triplet = {
    runwayNames = { ['native:1'] = '22L', ['native:2'] = '31', ['native:3'] = '04R' } } } } })
  s = bridge(W, 'e1')
  has(s.error, 'Triplet runway 2: label 31 inconsistent with native course')
  has(s.error, 'runways labelled 04R are ambiguous (native 1,3)', '22L is the 22 end of 04R: duplicate label')
  eq(names(field(s, '25').runways), 'native:1,native:2,native:3', 'nothing selectable, all geometry kept')
  W = newMission({ mutate = triplet, config = { airfields = { Triplet = { runwayNames = { ['native:1'] = '04L' } } } } })
  s = bridge(W, 'e1')
  has(s.error, 'Triplet: runways labelled 04 are ambiguous (native 2,3)', 'no auto L/R beside a labelled strip')
  eq(names(field(s, '25').runways), '04L,native:2,native:3', 'no duplicate 04L derived for the unlabelled pair')
  eq(field(s, '25').runways[1].name, '04L')
end)

check('a crossing strip with an unusable native label is still exported as occupancy geometry', function()
  local W = newMission({ config = false, mutate = function(W)
    quiet(W)
    table.insert(W.ironwood.runways, runway('??', 0, 0, 0, 1800, 45))   -- crosses 09/27 at its centre
    table.insert(W.ironwood.runways, runway('', 180, 0, 0, 1800, 45))   -- other native end of the same strip
  end })
  local s = bridge(W, 'e1')
  local iron = field(s, '21')
  eq(names(iron.runways), '09,native:3', 'labelled runway plus one geometry-only crossing strip')
  local cross = find(iron.runways, 'id', 'native:3')
  eq(cross.name, '')
  eq(cross.opposite_name, nil)
  eq(cross.closed, nil)
  near(cross.heading_deg, 3, 0.5, 'true heading of the native course (grid north + 3)')
  eq(cross.length_m, 1800)
  eq(cross.width_m, 45)
  eq(cross.point.x, 0)
  eq(iron.runways[1].name, '09', 'valid runway unaffected')
  has(s.error, 'Ironwood runway 3: unusable runway label ??')
  has(s.error, 'Ironwood runway 4: unusable runway label')
end)

check('weather, position and ownership are live per field; calm has no direction; capture shows', function()
  local W = newMission({ mutate = quiet, config = false })
  local s = bridge(W, 'e1')
  local iron, maple = field(s, '21'), field(s, '22')
  eq(iron.alive, true)
  near(iron.wind_from_deg, 3, 0.5, 'true direction wind is FROM')
  eq(iron.wind_speed_mps, 5)
  eq(iron.altimeter_inhg, 30, 'altimeter from DCS sea-level pressure at the field')
  near(iron.lat, 42, 1e-6)
  eq(iron.point.y, 100)
  eq(maple.wind_speed_mps, 0, 'calm')
  eq(maple.wind_from_deg, nil, 'calm has no invented direction')
  W.ironwood.coalition = 1
  W.maple.ownerError = true
  W.badfield.alive = false
  W.wind = function(p) return { x = 0, y = 0, z = p.x > 90000 and 7 or 1e9 } end
  s = bridge(W, 'e1')
  eq(field(s, '21').coalition, 1, 'capture visible next snapshot')
  eq(field(s, '21').wind_speed_mps, nil, 'impossible wind dropped, not clamped')
  eq(field(s, '21').wind_from_deg, nil)
  eq(field(s, '22').coalition, 0, 'unreadable ownership is never a side')
  has(s.error, 'Maple: ownership unavailable')
  near(field(s, '22').wind_from_deg, 273, 1, 'wind toward grid east is from true 273')
  eq(field(s, '23').alive, false)
  eq(field(s, '23').point, nil, 'no fabricated position')
  eq(field(s, '23').id, '23', 'identity kept')
end)

check('native radio context is joined by airdrome id, merged per channel, bad entries rejected alone', function()
  local W = newMission({ mutate = quiet, config = false })
  local radios = {}
  for _, r in ipairs(DEFAULT_RADIOS) do radios[#radios + 1] = r end
  radios[#radios + 1] = { airfield_id = '21', frequency_hz = 121.5, modulation = 0, roles = ALL_ROLES, aliases = {} }
  radios[#radios + 1] = { airfield_id = '21', frequency_hz = 124000000, modulation = 2, roles = ALL_ROLES }
  radios[#radios + 1] = { airfield_id = '21', frequency_hz = 125000000, modulation = 0, roles = { 'atis' } }
  radios[#radios + 1] = { airfield_id = '23', frequency_hz = 126000000, modulation = 0, roles = { 'tower' },
                          aliases = { 'bad\255' } }
  radios[#radios + 1] = { airfield_id = '999', frequency_hz = 127000000, modulation = 0, roles = { 'tower' } }
  local s = bridge(W, 'e1', nil, contextFor('Caucasus', radios))
  local iron, maple = field(s, '21'), field(s, '22')
  eq(count(iron.radios), 2)
  eq(iron.radios[1].frequency_hz, 121000000)
  eq(iron.radios[1].modulation, 0)
  eq(table.concat(iron.radios[1].roles, ','), 'ground,tower,approach')
  eq(iron.radios[2].frequency_hz, 251000000)
  eq(count(maple.radios), 1, 'same channel and modulation merged into one radio')
  eq(table.concat(maple.radios[1].roles, ','), 'ground,tower')
  eq(table.concat(maple.radios[1].aliases, ','), 'Maple,Maple Ground')
  eq(count(field(s, '23').radios), 0, 'no frequency invented')
  has(s.error, '4 native radio entries rejected')
  has(s.error, '1 native radio entries for airdromes not in this mission')
end)

check('missing, malformed or other-theatre radio context leaves fields up but radio-less', function()
  local W = newMission({ mutate = quiet, config = false })
  for _, case in ipairs({
    { false, 'native radio data unavailable' },
    { 'not json', 'native radio context malformed' },
    { contextFor('Syria'), 'theatre Syria does not match mission theatre Caucasus' },
    { encode({ radios = DEFAULT_RADIOS }), 'does not match mission theatre' },
    { contextFor('Caucasus', {}, 'native Radio.lua not readable'), 'native radio data: native Radio.lua not readable' },
  }) do
    local s = bridge(W, 'e1', nil, case[1])
    eq(count(s.airfields), 3, 'airfields still reported')
    for _, f in ipairs(s.airfields) do eq(count(f.radios), 0, 'no radios from ' .. tostring(case[1])) end
    has(s.error, case[2])
    eq(count(field(s, '21').runways), 1, 'geometry unaffected')
  end
  local s = bridge(W, 'e1')
  eq(count(field(s, '21').radios), 2, 'valid context applies again')
end)

check('human catalog has identities only; watch applies once from the latest accepted response', function()
  local W = newMission()
  local s1 = bridge(W, 'e1')
  eq(count(s1.players), 2, 'humans only')
  local lead = player(s1, '301')
  eq(lead.unit_name, LEAD)
  eq(lead.callsign, 'Enfield11')
  eq(lead.modex, '011')
  eq(lead.human, true)
  eq(player(s1, '401'), nil, 'AI not catalogued')
  eq(count(s1.aircraft), 0, 'no aircraft state without watch')
  eq(W.polls[LEAD], nil, 'catalog never polls movement')
  local stale = accept(W, {})
  local s2 = bridge(W, 'e1', accept(W, { watchOf(s1, '301', '21') }))
  local a = aircraft(s2, '301')
  assert(a, 'watched aircraft reported')
  eq(a.airfield_id, '21', 'subscription binding echoed')
  eq(a.incarnation, lead.incarnation)
  eq(a.in_air, false)
  eq(a.point.z, -500)
  near(a.heading_deg, 3, 0.5, 'true heading of a grid-north aircraft')
  near(a.lat, 42 + 500 * math.sin(CONVERGENCE) / 111000, 1e-6)
  eq(a.ground_airfield_id, '21', 'mission client slot airdromeId')
  eq(count(a.events), 0)
  local s3 = bridge(W, 'e1', stale)
  assert(aircraft(s3, '301'), 'stale response cannot clear the watch')
  local old = accept(W, { watchOf(s3, '301', '21') })
  local s4 = bridge(W, 'e1', accept(W, {}))
  eq(aircraft(s4, '301'), nil, 'empty accepted watch releases')
  eq(aircraft(bridge(W, 'e1', old), '301'), nil, 'replayed response cannot resurrect membership')
  for _, bad in ipairs({
    accept(W, { watchOf(W.last, '301', '21') }, { epoch = 'e0' }),
    accept(W, { watchOf(W.last, '301', '21') }, { accepted = false }),
    accept(W, { watchOf(W.last, '301', '21') }, { version = 2 }),
    accept(W, { { unit_id = '301', incarnation = 'forged', airfield_id = '21' } }),
    accept(W, { watchOf(W.last, '301', '50') }),        -- FARP id is not an airfield
    accept(W, { watchOf(W.last, '301', '24') }),        -- rejected airdrome
    accept(W, { { unit_id = '401', incarnation = '401:1', airfield_id = '21' } }),
    accept(W, { { unit_id = '301', incarnation = player(W.last, '301').incarnation, carrier_id = '21' } }),
    'not json', string.rep('x', 70000),
  }) do
    if type(bad) == 'table' then bad.seq = W.last.seq end
    eq(count(bridge(W, 'e1', bad).aircraft), 0, 'invalid response or watch entry ignored')
  end
  local s6 = bridge(W, 'e1', accept(W, { watchOf(W.last, '301', '21') }))
  assert(aircraft(s6, '301'), 'fresh watch works')
  local body = encode(accept(W, { watchOf(s6, '301', '21') }))
  bridge(W, 'e1', body)
  assert(aircraft(bridge(W, 'e1', body), '301'), 'repeated body neither renews nor clears')
  eq(W.polls[WING], nil, 'unregistered human never polled')
  eq(W.polls[AI], nil, 'AI never polled')
  eq(#W.forbidden, 0)
end)

check('watch lease expires without fresh accepted responses and polling stops', function()
  local W = newMission()
  enrol(W, 'e1', '301', '21')
  local renewal = accept(W, { watchOf(W.last, '301', '21') })
  W.advance(10)
  local s = bridge(W, 'e1')
  assert(aircraft(s, '301'), 'lease valid at 10 s')
  for _ = 1, 6 do
    W.advance(1)
    s = bridge(W, 'e1', renewal)
  end
  eq(aircraft(s, '301'), nil, 'watch expired after lease')
  local polls = W.polls[LEAD]
  W.advance(5)
  bridge(W, 'e1')
  eq(W.polls[LEAD], polls, 'no polling after lease expiry')
end)

check('respawn sends one final birth event, then a new incarnation never inherits the watch', function()
  local W = newMission()
  local s = enrol(W, 'e1', '301', '21')
  local first = player(s, '301').incarnation
  W.lead.player = 'Goose'
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '21') }))
  local second = player(s, '301').incarnation
  assert(second ~= first, 'player change renews incarnation')
  eq(aircraft(s, '301'), nil, 'old-incarnation watch rejected')
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '21') }))
  assert(aircraft(s, '301'), 'new incarnation watchable')
  W.event(EVENT.S_EVENT_BIRTH, W.lead, W.maple)
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '21') }))
  eq(player(s, '301').incarnation, second, 'identity kept for the final snapshot')
  eq(kinds(aircraft(s, '301')), 'birth@22', 'final birth event with the native place')
  s = bridge(W, 'e1', accept(W, { { unit_id = '301', incarnation = second, airfield_id = '21' } }))
  assert(player(s, '301').incarnation ~= second, 'respawn renews incarnation')
  eq(aircraft(s, '301'), nil, 'respawned aircraft not tracked')
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '21') }))
  eq(aircraft(s, '301').ground_airfield_id, '22', 'birth place is the new ground airfield')
end)

check('birth before the player name is set resets airdrome identity to the slot spawn', function()
  local W = newMission()
  bridge(W, 'e1')
  W.event(EVENT.S_EVENT_LAND, W.lead, W.maple)        -- previous occupant landed at Maple
  W.lead.player = nil
  bridge(W, 'e1')
  W.lead.x, W.lead.z = 90000, 90000                  -- nowhere near any field
  W.event(EVENT.S_EVENT_BIRTH, W.lead)                -- new client, getPlayerName still nil
  W.lead.player = 'Viper'
  local s = bridge(W, 'e1')
  eq(player(s, '301').player_name, 'Viper')
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '21') }))
  eq(aircraft(s, '301').ground_airfield_id, '21', 'slot airdromeId after birth reset, not old Maple landing')
  eq(W.polls[AI], nil, 'AI never polled')
end)

check('land/takeoff carry the actual native place; ships never count; history beats position', function()
  local W = newMission()
  W.lead.air = true                                   -- enrolled airborne, so the touchdown is news
  enrol(W, 'e1', '301', '21')
  W.lead.air = false
  W.event(EVENT.S_EVENT_RUNWAY_TOUCH, W.lead, W.maple)
  W.event(EVENT.S_EVENT_LAND, W.lead, W.maple)
  local s = bridge(W, 'e1')
  eq(kinds(aircraft(s, '301')), 'land@22', 'single foreign land event with actual place')
  eq(aircraft(s, '301').ground_airfield_id, '22', 'landed at Maple although sitting on Ironwood geometry')
  eq(aircraft(s, '301').airfield_id, '21', 'subscription unchanged')
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '21') }))
  eq(kinds(aircraft(s, '301')), '', 'acknowledged events not resent')
  W.lead.air = true
  W.event(EVENT.S_EVENT_RUNWAY_TAKEOFF, W.lead, W.maple)
  W.event(EVENT.S_EVENT_TAKEOFF, W.lead, W.maple)
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '21') }))
  eq(kinds(aircraft(s, '301')), 'takeoff@22')
  eq(aircraft(s, '301').ground_airfield_id, nil, 'airborne: no ground airfield')
  W.lead.air = false
  W.event(EVENT.S_EVENT_LAND, W.lead, W.ship)         -- ship place shares Ironwood's numeric id
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '21') }))
  eq(kinds(aircraft(s, '301')), 'land', 'ship landing has no airfield place')
  eq(aircraft(s, '301').ground_airfield_id, nil, 'no proximity override of a known non-airfield landing')
  local again = bridge(W, 'e1')
  eq(kinds(aircraft(again, '301')), 'land', 'unacknowledged event resent with same id')
  eq(aircraft(again, '301').events[1].id, aircraft(s, '301').events[1].id)
end)

check('ground membership without history needs a unique native runway or parking footprint', function()
  local W = newMission()
  local s = bridge(W, 'e1')
  W.wing.x, W.wing.z = 10, 900                        -- on Ironwood's runway, no events, no spawn airdrome
  s = bridge(W, 'e1', accept(W, { watchOf(s, '302', '22') }))
  eq(aircraft(s, '302').ground_airfield_id, '21', 'native runway footprint, regardless of subscription')
  W.wing.x, W.wing.z = 100510, 505                    -- Maple parking spot
  s = bridge(W, 'e1', accept(W, { watchOf(s, '302', '22') }))
  eq(aircraft(s, '302').ground_airfield_id, '22')
  W.wing.x, W.wing.z = 0, 1000                        -- Ironwood runway AND Badfield parking: ambiguous
  s = bridge(W, 'e1', accept(W, { watchOf(s, '302', '22') }))
  eq(aircraft(s, '302').ground_airfield_id, nil, 'ambiguous footprint is no proof')
  W.wing.x, W.wing.z = 2250, 3000                     -- between Badfield strips: outside every footprint
  s = bridge(W, 'e1', accept(W, { watchOf(s, '302', '22') }))
  eq(aircraft(s, '302').ground_airfield_id, nil, 'near a field is not on it')
  W.wing.x, W.wing.z = 2500, 3010                     -- unlabelled Badfield strip still physical geometry
  s = bridge(W, 'e1', accept(W, { watchOf(s, '302', '22') }))
  eq(aircraft(s, '302').ground_airfield_id, '23')
  W.wing.air = true
  s = bridge(W, 'e1', accept(W, { watchOf(s, '302', '22') }))
  eq(aircraft(s, '302').ground_airfield_id, nil, 'airborne overrides footprint')
  eq(W.polls[AI], nil, 'AI parked on Ironwood parking never polled')
end)

check('AI and unregistered lifecycle events leave no buffers and trigger no polling', function()
  local W = newMission()
  bridge(W, 'e1')
  W.event(EVENT.S_EVENT_BIRTH, W.ai, W.ironwood)
  W.event(EVENT.S_EVENT_LAND, W.ai, W.ironwood)
  W.event(EVENT.S_EVENT_TAKEOFF, W.ai, W.ironwood)
  W.event(EVENT.S_EVENT_DEAD, W.ai)
  W.event(EVENT.S_EVENT_RUNWAY_TOUCH, W.wing, W.maple)
  W.event(EVENT.S_EVENT_RUNWAY_TAKEOFF, W.wing, W.maple)
  local s = enrol(W, 'e1', '302', '22')
  eq(kinds(aircraft(s, '302')), '', 'pre-registration events are not replayed')
  eq(aircraft(s, '401'), nil)
  eq(W.polls[AI], nil, 'AI never polled')
  eq(W.polls[LEAD], nil, 'other human never polled')
  eq(#W.forbidden, 0)
end)

check('dead, eject and disconnect send one final event then release the identity', function()
  for _, case in ipairs({ { EVENT.S_EVENT_DEAD, 'dead', true }, { EVENT.S_EVENT_EJECTION, 'eject', false },
                          { EVENT.S_EVENT_PLAYER_LEAVE_UNIT, 'disconnect', true } }) do
    local W = newMission()
    local s = enrol(W, 'e1', '301', '21')
    local incarnation = player(s, '301').incarnation
    if case[1] == EVENT.S_EVENT_PLAYER_LEAVE_UNIT then W.lead.player = nil end
    if case[1] == EVENT.S_EVENT_DEAD then W.lead.alive = false end
    W.event(case[1], W.lead)
    W.event(case[1], W.lead)
    s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '21') }))
    eq(player(s, '301').incarnation, incarnation, case[2] .. ': identity kept for the final snapshot')
    eq(kinds(aircraft(s, '301')), case[2], case[2] .. ': one terminal event')
    eq(aircraft(s, '301').point.z, -500, case[2] .. ': last real sample')
    s = bridge(W, 'e1', accept(W, { { unit_id = '301', incarnation = incarnation, airfield_id = '21' } }))
    eq(aircraft(s, '301'), nil, case[2] .. ': released')
    if case[3] then
      eq(player(s, '301'), nil, case[2] .. ': gone from catalog')
    else
      assert(player(s, '301').incarnation ~= incarnation, case[2] .. ': still-listed unit gets a new incarnation')
    end
  end
end)

check('out-of-range samples drop only the affected record, never clamped or zeroed', function()
  local W = newMission()
  local s = bridge(W, 'e1')
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '21'), watchOf(s, '302', '22') }))
  eq(count(s.aircraft), 2)
  W.lead.vx = 2500
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '21'), watchOf(s, '302', '22') }))
  eq(aircraft(s, '301'), nil, 'invalid velocity sample skipped')
  assert(aircraft(s, '302'), 'other watched aircraft still reported')
  W.lead.vx = 10
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '21'), watchOf(s, '302', '22') }))
  eq(aircraft(s, '301').velocity.x, 10, 'valid sample reported again')
end)

check('epoch change resets sequence and watch without reusing incarnations; mission end stops', function()
  local W = newMission()
  W.lead.air = true
  local s = enrol(W, 'e1', '301', '21')
  local old = player(s, '301').incarnation
  W.lead.air = false
  W.event(EVENT.S_EVENT_LAND, W.lead, W.ironwood)
  local oldEvent = aircraft(bridge(W, 'e1'), '301').events[1].id
  local reply = accept(W, { watchOf(W.last, '301', '21') })
  s = bridge(W, 'e2', reply)
  eq(s.seq, 1, 'sequence restarted')
  eq(count(s.aircraft), 0, 'previous epoch watch not applied')
  assert(player(s, '301').incarnation ~= old, 'incarnations not reused across epochs')
  s = enrol(W, 'e2', '301', '21')
  W.lead.air = true
  W.event(EVENT.S_EVENT_TAKEOFF, W.lead, W.ironwood)
  assert(aircraft(bridge(W, 'e2'), '301').events[1].id > oldEvent, 'event ids never reused')
  eq(bridge(W, 'bad epoch!'), nil, 'epoch validated')
  W.event(EVENT.S_EVENT_MISSION_END)
  eq(W.env.OverlordAirfield.bridge('e2', '', W.context), nil, 'stopped')
end)

check('missing mission date is reported, never fabricated; script runs without config', function()
  local W = newMission({ mutate = quiet, config = false })
  W.mission.date = nil
  local s = bridge(W, 'e1')
  eq(s.date, nil, 'no invented date')
  has(s.error, 'mission date unavailable in env.mission')
  assert(not s.error:find('config', 1, true), 'no config errors without config')
  eq(field(s, '21').runways[1].closed, false, 'no closures without config')
end)

-- Server hook ----------------------------------------------------------------------------

-- Native front matter: real Radio.lua files dofile the two constant tables first.
local RADIO_FILE = [[
dofile('Scripts/World/Radio/ModulationTypes.lua')
dofile('Scripts/World/Radio/FrequencyBands.lua')
local gettext = require("i_18n")
local _ = gettext.translate
radioTableFormat = 3

radio =
{
	[1] =
	{
		radioId = 'airfield21_0';
		role = {"ground", "tower", "approach"};
		callsign = {{["common"] = {_("Ironwood"), "Ironwood"}, ["nato"] = {_("Ironwood Nato"), "Ironwood Nato"}}};
		frequency = {[HF] = {MODULATIONTYPE_AM, 4250000.000000}, [UHF] = {MODULATIONTYPE_AM, 251000000.000000}, [VHF_LOW] = {MODULATIONTYPE_FM, 39000000.000000}, [VHF_HI] = {MODULATIONTYPE_AM, 121000000.000000}};
		sceneObjects = {'t:1'};
	};
	[2] =
	{
		radioId = 'airfield22_0';
		role = {"ground", "tower", "approach"};
		callsign = {{["common"] = {_("Maple"), "Maple"}}};
		frequency = {[VHF_HI] = {MODULATIONTYPE_AM, 121000000.000000}};
	};
	[3] =
	{
		radioId = 'airfield23_0';
		role = {"ground", "tower", "approach"};
		callsign = {{["common"] = {_("Badfield"), "Badfield"}}};
		frequency = {};
	};
	[4] = { radioId = 'airfield21_1'; role = {"atis"}; frequency = {[VHF_HI] = {MODULATIONTYPE_AM, 127000000}}; };
	[5] = { radioId = 'airfield999_0'; role = {"tower"}; frequency = {[UHF] = {MODULATIONTYPE_AM, 260000000}}; };
	[6] = { radioId = 'beacon7_0'; role = {"tower"}; frequency = {[UHF] = {MODULATIONTYPE_AM, 261000000}}; };
	[7] = { radioId = 'airfield22_1'; role = {"tower"}; frequency = {[UHF] = {MODULATIONTYPE_AMFM, 262000000}, [VHF_HI] = {MODULATIONTYPE_AM, 121500000.5}}; };
}
]]

local function newHook(W, opts)
  opts = opts or {}
  local H = { real = 0, requests = {}, calls = {}, logs = {}, conns = {}, stall = {}, replies = {}, opens = 0,
              paths = {}, reads = {} }
  local dir = tempDir()
  H.dir = dir
  local config = 'return { host = "127.0.0.1", port = 8081, bridgeToken = "bridge-secret", apiToken = "api-secret",'
    .. ' interval = 1, timeout = 3, state = ' .. string.format('%q', opts.state or 'server') .. ', features = '
    .. (opts.features or '{ "airfields" }')
    .. (opts.extra and (', ' .. opts.extra) or '') .. ' }'
  writeFile(dir .. '/Config/overlord-hook.lua', config)
  for name, content in pairs(opts.companions or {}) do writeFile(dir .. '/Scripts/Overlord/' .. name, content) end

  local function respond(conn)
    local split = conn.data:find('\r\n\r\n', 1, true)
    local headers, body = conn.data:sub(1, split - 1), conn.data:sub(split + 4)
    local request = { path = headers:match('^POST (%S+) HTTP/1%.1'), headers = headers, body = decode(body) }
    H.requests[#H.requests + 1] = request
    if H.stall[request.path] then return end
    local payload = H.replies[request.path] and H.replies[request.path](request)
    payload = payload or encode({ version = 1, accepted = true, epoch = request.body.epoch, seq = request.body.seq,
                                  watch = {}, commands = {}, cancel = {} })
    conn.response = 'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: ' .. #payload
      .. '\r\n\r\n' .. payload
    conn.offset = 0
  end

  local socket = {}
  function socket.tcp()
    local conn = { data = '', closed = false }
    H.conns[#H.conns + 1] = conn
    function conn:settimeout() end
    function conn:connect() return nil, 'timeout' end
    function conn:send(data, i)
      self.data = self.data .. data:sub(i)
      respond(self)
      return #data
    end
    function conn:receive(n)
      if not self.response then return nil, 'timeout', '' end
      if self.offset >= #self.response then return nil, 'closed', '' end
      local chunk = self.response:sub(self.offset + 1, self.offset + math.min(n, 4096))
      self.offset = self.offset + #chunk
      return nil, 'timeout', chunk
    end
    function conn:close() self.closed = true end
    return conn
  end
  function socket.select(_, writable) return {}, writable end

  local env = setmetatable({
    DCS = {
      isServer = function() return true end,
      getRealTime = function() return H.real end,
      setUserCallbacks = function(cb) H.callbacks = cb end,
      getCurrentMission = function() return { mission = { theatre = opts.theatre or W.mission.theatre } } end,
    },
    net = {
      -- DCS keeps mission scripts in the "server" Lua state; the "mission" state is the mission
      -- description, where world (and every companion module) is nil.
      dostring_in = function(state, code)
        H.calls[#H.calls + 1] = { state = state, code = code }
        local target = state == 'server' and W.env
          or state == 'mission' and setmetatable({ type = type, pcall = pcall, loadstring = loadstring }, {})
        if not target then return '', false end
        local ok, result = pcall(compile(code, '=dostring', target))
        return tostring(result), ok
      end,
      lua2json = encode,
    },
    debug = opts.debug,   -- nil: the real debug library (through _G)
    pcall = opts.pcall,   -- nil: the real pcall (through _G)
    -- The hook's io file objects have read/close but no seek.
    io = { open = function(path, mode)
      H.opens = H.opens + 1
      H.opened = path
      H.paths[#H.paths + 1] = path
      local file = io.open(path, mode)
      if not file then return nil end
      return {
        read = function(_, what)
          H.reads[#H.reads + 1] = what
          if type(what) == 'number' and opts.noNumericRead then error('bad argument #1 to read') end
          return file:read(what)
        end,
        close = function() file:close() end,
      }
    end },
    log = { write = function(_, _, m) H.logs[#H.logs + 1] = m end, ERROR = 1, WARNING = 2, INFO = 3 },
    lfs = { writedir = function() return dir .. '/' end,
            currentdir = function() return opts.installDir or (dir .. '/dcs') end },
    package = { path = '', cpath = '' },
    require = function(name) assert(name == 'socket', 'unexpected module ' .. name); return socket end,
  }, { __index = _G })
  H.env = env
  compile(readFile(ROOT .. 'dcs/overlord_hook.lua'), '@overlord_hook.lua', env)()
  function H.run(seconds)
    local steps = math.floor(seconds / 0.0625 + 0.5)
    for _ = 1, steps do
      H.real = H.real + 0.0625
      H.callbacks.onSimulationFrame()
    end
  end
  function H.on(path)
    local out = {}
    for _, request in ipairs(H.requests) do if request.path == path then out[#out + 1] = request end end
    return out
  end
  function H.cleanup() os.execute('rm -rf "' .. dir .. '"') end
  return H
end

local function radioFileConfig(path) return 'airfieldRadioFiles = { Caucasus = ' .. string.format('%q', path) .. ' }' end

check('hook reads the configured native Radio.lua privately and joins radios by airdrome id', function()
  local W = newMission({ mutate = quiet, config = false })
  local dir = tempDir()
  writeFile(dir .. '/terrain/Radio.lua', RADIO_FILE)
  local H = newHook(W, { extra = radioFileConfig(dir .. '/terrain/Radio.lua') })
  H.callbacks.onSimulationStart()
  H.run(3)
  local requests = H.on('/airfield/exchange')
  eq(#requests, 3, 'airfield exchanges')
  local body = requests[1].body
  local iron = field(body, '21')
  local tuples = {}
  for _, r in ipairs(iron.radios) do
    tuples[#tuples + 1] = r.frequency_hz .. '/' .. r.modulation .. '/' .. table.concat(r.roles, '+')
  end
  eq(table.concat(tuples, ','), '4250000/0/ground+tower+approach,39000000/1/ground+tower+approach,'
    .. '121000000/0/ground+tower+approach,251000000/0/ground+tower+approach', 'every native band, ATIS excluded')
  eq(table.concat(iron.radios[1].aliases, ','), 'Ironwood,Ironwood Nato')
  eq(field(body, '22').radios[1].frequency_hz, 121000000, 'shared native channel kept per field')
  eq(count(field(body, '22').radios), 1, 'invalid modulation constant and fractional Hz rejected')
  eq(count(field(body, '23').radios), 0, 'empty native frequency list stays empty')
  has(body.error, 'native radio data: 2 native airfield radio records publish no usable frequency')
  has(body.error, '1 native radio entries for airdromes not in this mission')
  for _, call in ipairs(H.calls) do
    assert(call.code:match('^return OverlordAirfield and OverlordAirfield%.bridge%(".-",".-",".*"%)$'), 'fixed call')
    assert(not call.code:find(dir, 1, true), 'path never passed to the mission')
    assert(not call.code:find('secret', 1, true), 'token never passed to the mission')
  end
  for _, request in ipairs(requests) do
    assert(request.headers:find('\r\nX%-DCS%-Token: bridge%-secret'), 'bridge token')
    assert(request.headers:find('\r\nAuthorization: Bearer api%-secret'), 'api token')
  end
  for _, line in ipairs(H.logs) do assert(not line:find('secret'), 'token logged') end
  eq(H.opens, 1, 'native file read once per epoch')
  H.callbacks.onSimulationStop()
  H.callbacks.onSimulationStart()
  H.run(1)
  eq(H.opens, 2, 're-read for the next mission')
  eq(#W.forbidden, 0)
  H.cleanup()
  os.execute('rm -rf "' .. dir .. '"')
end)

check('hook falls back to the native terrain path under the DCS install directory', function()
  local W = newMission()
  local H = newHook(W)
  writeFile(H.dir .. '/dcs/Mods/terrains/Caucasus/Radio.lua', RADIO_FILE)
  H.callbacks.onSimulationStart()
  H.run(1)
  eq(H.opened, H.dir .. '/dcs/Mods/terrains/Caucasus/Radio.lua', 'native default path')
  eq(count(field(H.on('/airfield/exchange')[1].body, '21').radios), 4)
  H.cleanup()
end)

check('missing native file or unknown theatre gives explicit radio-less fields, never guesses', function()
  local W = newMission({ mutate = quiet, config = false })
  local H = newHook(W)
  H.callbacks.onSimulationStart()
  H.run(1)
  local body = H.on('/airfield/exchange')[1].body
  has(body.error, 'native Radio.lua not readable')
  eq(count(field(body, '21').radios), 0)
  eq(count(field(body, '21').runways), 1, 'geometry still served')
  H.cleanup()
  H = newHook(W, { theatre = '../Caucasus' })
  H.callbacks.onSimulationStart()
  H.run(1)
  body = H.on('/airfield/exchange')[1].body
  has(body.error, 'mission theatre unavailable')
  eq(H.opens, 0, 'unsafe theatre name never becomes a path')
  H.cleanup()
end)

check('hostile Radio.lua content cannot escape the data sandbox or leave state behind', function()
  local dumped = string.dump(function() radio = {} end)
  local marker = os.tmpname()
  os.remove(marker)
  local cases = {
    { 'radio = {} os.exit(3)', 'execution refused' },
    { 'radio = {} io.open(' .. string.format('%q', marker) .. ', "w")', 'execution refused' },
    { 'radio = {} dofile("x.lua")', 'execution refused' },
    { 'dofile("Scripts/World/Radio/Other.lua") radio = {}', 'execution refused' },
    { 'dofile("Scripts/World/Radio/../../../Config/overlord-hook.lua") radio = {}', 'execution refused' },
    { 'dofile() radio = {}', 'execution refused' },
    { 'radio = {} require("socket")', 'execution refused' },
    { 'radio = {} loadstring("HACKED = true")()', 'execution refused' },
    { 'radio = { ("x"):rep(1073741824) }', 'execution refused' },
    { 'radio = {} getmetatable("").__index = nil', 'execution refused' },
    { 'radio = {} while true do end', 'execution refused' },
    { 'HACKED = true', 'no radio table' },
    { 'radio = {', 'syntax error' },
    { dumped, 'precompiled chunk refused' },
    { '', 'native Radio.lua read failed' },
  }
  for _, case in ipairs(cases) do
    local W = newMission({ mutate = quiet, config = false })
    local H = newHook(W)
    writeFile(H.dir .. '/dcs/Mods/terrains/Caucasus/Radio.lua', case[1])
    H.callbacks.onSimulationStart()
    H.run(1)
    local body = H.on('/airfield/exchange')[1].body
    has(body.error, 'native radio data: ' .. case[2], 'case ' .. case[1]:sub(1, 30))
    for _, f in ipairs(body.airfields) do eq(count(f.radios), 0, 'no radios from hostile file') end
    eq(rawget(_G, 'HACKED'), nil, 'no global written')
    eq(('ab'):rep(2), 'abab', 'string methods restored')
    eq(debug.gethook(), nil, 'instruction hook removed')
    eq(#W.forbidden, 0)
    H.cleanup()
  end
  local f = io.open(marker, 'r')
  if f then f:close() end
  eq(f, nil, 'no file created from Radio.lua')
end)

check('without the debug hook API Radio.lua is never run; other features keep running', function()
  local W = newMission({ mutate = quiet, config = false, others = true })
  local dir = tempDir()
  writeFile(dir .. '/Radio.lua', RADIO_FILE)
  local H = newHook(W, { features = '{ "jtac", "carriers", "airfields" }', extra = radioFileConfig(dir .. '/Radio.lua'),
                         debug = {} })
  H.callbacks.onSimulationStart()
  H.run(2)
  local body = H.on('/airfield/exchange')[1].body
  has(body.error, 'native radio data: instruction limit unavailable')
  for _, f in ipairs(body.airfields) do eq(count(f.radios), 0, 'valid file still refused without a limiter') end
  eq(#H.on('/dcs/exchange'), 2, 'jtac unaffected')
  eq(#H.on('/carrier/exchange'), 2, 'carriers unaffected')
  eq(#H.on('/airfield/exchange'), 2, 'airfield geometry still served')
  eq(('ab'):rep(2), 'abab', 'string methods untouched')
  H.cleanup()
  os.execute('rm -rf "' .. dir .. '"')
end)

-- Debug double: records the hook chain without counting instructions, so the moment the limit
-- "fires" is chosen by the test instead of by timing.
local function fakeDebug()
  local fake = { mask = 'c', count = 123 }
  function fake.prior() end
  fake.current = fake.prior
  function fake.gethook() return fake.current, fake.mask, fake.count end
  function fake.sethook(fn, mask, count)
    fake.current, fake.mask, fake.count = fn, mask, count
    if fn and fn ~= fake.prior then fake.limit = fn end
  end
  return fake
end

check('a limit hit right after the chunk returns still restores the previous hook and string methods', function()
  for _, boundary in ipairs({ false, true }) do
    local W = newMission({ mutate = quiet, config = false, others = true })
    local dir = tempDir()
    writeFile(dir .. '/Radio.lua', RADIO_FILE)
    local fake, fired = fakeDebug(), false
    local realPcall = pcall
    -- The first pcall after the limit is installed is pcall(chunk): let the chunk return, then
    -- fire the limit at the very next instruction, outside every pcall of runRadioData.
    local function boundaryPcall(fn, ...)
      if fake.limit and not fired and fn ~= fake.sethook then
        fired = true
        realPcall(fn, ...)
        fake.limit()
        error('limit callback returned')
      end
      return realPcall(fn, ...)
    end
    local H = newHook(W, { features = '{ "jtac", "carriers", "airfields" }', extra = radioFileConfig(dir .. '/Radio.lua'),
                           debug = fake, pcall = boundary and boundaryPcall or nil })
    H.callbacks.onSimulationStart()
    H.run(2)
    local body = H.on('/airfield/exchange')[1].body
    eq(fake.current, fake.prior, 'previous hook restored')
    eq(fake.mask, 'c', 'previous hook mask restored')
    eq(fake.count, 123, 'previous hook count restored')
    eq(('ab'):rep(2), 'abab', 'string methods restored')
    if boundary then
      eq(fired, true, 'limit fired at the return boundary')
      has(body.error, 'native radio data: native radio data failed')
      for _, f in ipairs(body.airfields) do eq(count(f.radios), 0, 'no radios from an interrupted load') end
    else
      eq(count(field(body, '21').radios), 4, 'normal path loads radios under the limit')
    end
    eq(#H.on('/dcs/exchange'), 2, 'jtac keeps exchanging')
    eq(#H.on('/carrier/exchange'), 2, 'carriers keep exchanging')
    eq(#H.on('/airfield/exchange'), 2, 'airfields keep exchanging')
    H.cleanup()
    os.execute('rm -rf "' .. dir .. '"')
  end
end)

check('configured radio paths reject traversal, relative, network and non-Lua paths', function()
  for _, extra in ipairs({
    'airfieldRadioFiles = { Caucasus = "../Radio.lua" }',
    'airfieldRadioFiles = { Caucasus = "Mods/terrains/Caucasus/Radio.lua" }',
    'airfieldRadioFiles = { Caucasus = "\\\\\\\\server\\\\share\\\\Radio.lua" }',
    'airfieldRadioFiles = { Caucasus = "C:\\\\DCS\\\\..\\\\Radio.lua" }',
    'airfieldRadioFiles = { Caucasus = "/tmp/Radio.txt" }',
    'airfieldRadioFiles = { Caucasus = "/tmp/Radio\\n.lua" }',
    'airfieldRadioFiles = { ["../Caucasus"] = "/tmp/Radio.lua" }',
    'airfieldRadioFiles = { "/tmp/Radio.lua" }',
    'airfieldRadioFiles = "/tmp/Radio.lua"',
  }) do
    local H = newHook(newMission(), { extra = extra })
    H.callbacks.onSimulationStart()
    H.run(2)
    eq(#H.calls, 0, 'unsafe config disables the hook: ' .. extra)
    eq(#H.conns, 0, 'no sockets: ' .. extra)
    local logged = false
    for _, line in ipairs(H.logs) do if line:find('airfieldRadioFiles', 1, true) then logged = true end end
    assert(logged, 'reason logged')
    H.cleanup()
  end
end)

check('three features run on fixed routes; a stalled or failing airfield feature starves no other', function()
  local W = newMission({ others = true })
  local dir = tempDir()
  writeFile(dir .. '/Radio.lua', RADIO_FILE)
  local H = newHook(W, { features = '{ "jtac", "carriers", "airfields" }', extra = radioFileConfig(dir .. '/Radio.lua') })
  H.callbacks.onSimulationStart()
  H.replies['/airfield/exchange'] = function(request)
    local lead = find(request.body.players, 'unit_id', '301')
    return encode({ version = 1, accepted = true, epoch = request.body.epoch, seq = request.body.seq,
                    watch = { { unit_id = '301', incarnation = lead.incarnation, airfield_id = '21' } } })
  end
  H.run(3)
  local jtac, carriers, airfields = H.on('/dcs/exchange'), H.on('/carrier/exchange'), H.on('/airfield/exchange')
  eq(#jtac, 3)
  eq(#carriers, 3)
  eq(#airfields, 3)
  eq(airfields[1].body.epoch, jtac[1].body.epoch, 'one hook epoch')
  eq(airfields[1].body.seq, 1, 'own sequence')
  eq(find(airfields[2].body.aircraft, 'unit_id', '301').airfield_id, '21', 'airfield watch round trip')
  eq(count(carriers[2].body.aircraft), 0, 'airfield watch does not leak into carrier feature')
  for _, call in ipairs(H.calls) do
    assert(call.code:match('^return OverlordJTAC and OverlordJTAC%.bridge%(".-",".-"%)$')
      or call.code:match('^return OverlordCarrier and OverlordCarrier%.bridge%(".-",".-"%)$')
      or call.code:match('^return OverlordAirfield and OverlordAirfield%.bridge%(".-",".-",".*"%)$'),
      'fixed entry points, context only for airfields')
  end
  H.stall['/airfield/exchange'] = true
  local before = { jtac = #H.on('/dcs/exchange'), carriers = #H.on('/carrier/exchange') }
  H.run(2.6)
  eq(#H.on('/dcs/exchange') - before.jtac, 3, 'jtac keeps its schedule during airfield stall')
  eq(#H.on('/carrier/exchange') - before.carriers, 3, 'carriers keep their schedule during airfield stall')
  H.stall['/airfield/exchange'] = nil
  H.run(1.5)
  W.env.OverlordAirfield.bridge = function() error('mission failure') end
  local mark = #H.on('/airfield/exchange')
  before = { jtac = #H.on('/dcs/exchange'), carriers = #H.on('/carrier/exchange') }
  H.run(3)
  eq(#H.on('/airfield/exchange'), mark, 'failing airfield entry point posts nothing')
  eq(#H.on('/dcs/exchange') - before.jtac, 3, 'jtac unaffected')
  eq(#H.on('/carrier/exchange') - before.carriers, 3, 'carriers unaffected')
  local logged = false
  for _, line in ipairs(H.logs) do
    if line:find('[airfields]: mission entry point failed', 1, true) then logged = true end
  end
  assert(logged, 'feature-scoped error logged')
  eq(#W.forbidden, 0)
  H.cleanup()
  os.execute('rm -rf "' .. dir .. '"')
end)

check('hostile airfield response bytes stay a string literal; airfields-only hook calls nothing else', function()
  local W = newMission({ others = true })
  local H = newHook(W)
  H.callbacks.onSimulationStart()
  H.replies['/airfield/exchange'] = function() return '"]] ) os.exit(1) --\n\r\0' .. "'" .. ' {"version":1}' end
  H.run(2.1)
  local hostile
  for _, call in ipairs(H.calls) do if call.code:find('os.exit', 1, true) then hostile = call.code end end
  assert(hostile and hostile:match('^return OverlordAirfield and OverlordAirfield%.bridge%('), 'payload passed as data')
  eq(#W.forbidden, 0, 'no code from the response ran')
  for _, call in ipairs(H.calls) do
    assert(not call.code:find('OverlordJTAC', 1, true) and not call.code:find('OverlordCarrier', 1, true),
      'only the enabled feature is called')
  end
  H.cleanup()
end)

-- Auto-load ----------------------------------------------------------------------------

local ALL = '{ "jtac", "carriers", "airfields" }'
local COMPANIONS = { 'overlord_jtac.lua', 'overlord_carrier.lua', 'overlord_airfield.lua' }

local function realCompanions()
  local out = {}
  for _, name in ipairs(COMPANIONS) do out[name] = readFile(ROOT .. 'dcs/' .. name) end
  return out
end

local function opened(H, name)
  local n = 0
  for _, path in ipairs(H.paths) do if path == H.dir .. '/Scripts/Overlord/' .. name then n = n + 1 end end
  return n
end

local function logged(H, text)
  for _, line in ipairs(H.logs) do if line:find(text, 1, true) then return true end end
  return false
end

local function loaderCalls(H)
  local out = {}
  for _, call in ipairs(H.calls) do
    if call.code:find('^if Overlord%a+ ~= nil then return "present" end') then out[#out + 1] = call end
  end
  return out
end

check('auto-load is off by default: no companion is read and a bare mission stays unbridged', function()
  local W = newMission({ bare = true, config = false })
  local H = newHook(W, { features = ALL, companions = realCompanions() })
  H.callbacks.onSimulationStart()
  H.run(3)
  eq(#H.requests, 0, 'nothing to bridge')
  for _, name in ipairs(COMPANIONS) do eq(opened(H, name), 0, name .. ' never read') end
  eq(#loaderCalls(H), 0, 'no loader')
  eq(#W.loads, 0, 'nothing compiled')
  eq(#W.forbidden, 0)
  H.cleanup()
end)

check('auto-load puts the fixed static companions into a bare mission once per epoch', function()
  local W = newMission({ bare = true, config = false })
  local companions = realCompanions()
  local H = newHook(W, { features = ALL, extra = 'autoLoad = true', companions = companions })
  H.callbacks.onSimulationStart()
  H.run(1)
  eq(#loaderCalls(H), 0, 'mission start triggers get one interval first')
  H.run(2)
  eq(#H.on('/dcs/exchange'), 2, 'jtac bridged')
  eq(#H.on('/carrier/exchange'), 2, 'carriers bridged')
  eq(#H.on('/airfield/exchange'), 2, 'airfields bridged')
  eq(type(W.env.OverlordJTAC), 'table')
  eq(type(W.env.OverlordCarrier), 'table')
  eq(type(W.env.OverlordAirfield), 'table')
  local names = {}
  for _, load in ipairs(W.loads) do
    names[#names + 1] = load.name
    eq(load.source, companions[load.name:match('[^/]+$')], 'byte-exact companion source ' .. load.name)
  end
  table.sort(names)
  eq(table.concat(names, ','), '=Scripts/Overlord/overlord_airfield.lua,=Scripts/Overlord/overlord_carrier.lua,'
    .. '=Scripts/Overlord/overlord_jtac.lua', 'fixed chunk names')
  for _, name in ipairs(COMPANIONS) do
    eq(opened(H, name), 1, name .. ' read once')
    assert(logged(H, 'auto-loaded Scripts/Overlord/' .. name), name .. ' load logged')
  end
  for _, call in ipairs(H.calls) do
    assert(not call.code:find(H.dir, 1, true), 'path never passed to the mission')
    -- Companion sources mention "secrets" in prose; check the configured token values.
    assert(not call.code:find('bridge-secret', 1, true) and not call.code:find('api-secret', 1, true),
      'token never passed to the mission')
  end
  for _, line in ipairs(H.logs) do assert(not line:find('secret'), 'token logged') end
  H.run(3)
  eq(#W.loads, 3, 'answering modules are never reloaded')
  -- Next mission: only the module that is gone again is re-read, once.
  H.callbacks.onSimulationStop()
  rawset(W.env, 'OverlordJTAC', nil)
  H.callbacks.onSimulationStart()
  H.run(3)
  eq(opened(H, 'overlord_jtac.lua'), 2, 'new epoch re-reads the missing companion')
  eq(opened(H, 'overlord_carrier.lua'), 1, 'present carrier module not re-read')
  eq(type(W.env.OverlordJTAC), 'table', 'jtac reloaded')
  eq(#W.forbidden, 0)
  H.cleanup()
end)

check('auto-load never replaces a module the mission defines itself', function()
  local W = newMission({ bare = true, config = false })
  local own = { bridge = function() return nil end }   -- mission-configured, currently idle
  W.env.OverlordJTAC = own
  local H = newHook(W, { features = '{ "jtac" }', extra = 'autoLoad = true', companions = realCompanions() })
  H.callbacks.onSimulationStart()
  H.run(4)
  assert(rawget(W.env, 'OverlordJTAC') == own, 'mission module kept')
  eq(#W.loads, 0, 'nothing compiled over it')
  eq(#loaderCalls(H), 1, 'one attempt per epoch')
  assert(logged(H, 'mission defines OverlordJTAC; auto-load skipped'))
  eq(#H.requests, 0)
  H.cleanup()
  -- A mission that loads its own companion by trigger: the static file is never read.
  W = newMission({ others = true })
  H = newHook(W, { features = ALL, extra = 'autoLoad = true', companions = realCompanions() })
  H.callbacks.onSimulationStart()
  H.run(3)
  for _, name in ipairs(COMPANIONS) do eq(opened(H, name), 0, name .. ' not read') end
  eq(#H.on('/dcs/exchange'), 3)
  eq(#W.forbidden, 0)
  H.cleanup()
end)

check('companion bytes reach the mission only as one quoted literal, never matched or run in the hook', function()
  local hostile = '--[==[ "]] ) os.exit(1) --\r\n\0' .. "'" .. '\\" \\\\ ' .. string.char(1, 26, 255) .. ' ]==]\n'
    .. 'local m = {}\n'
    .. 'function m.bridge(epoch) return \'{"marker":"quoted","epoch":"\' .. epoch .. \'"}\' end\n'
    .. 'OverlordCarrier = m -- "]] \0\n'
  -- Names its module only in a comment: never treated as loaded by text matching.
  local decoy = '-- OverlordJTAC = (function() return {} end)()\nlocal unused = 1\n'
  local W = newMission({ bare = true, config = false })
  local H = newHook(W, { features = '{ "jtac", "carriers" }', extra = 'autoLoad = true, autoLoadFiles = { "evil.lua" }',
                         companions = { ['overlord_carrier.lua'] = hostile, ['overlord_jtac.lua'] = decoy,
                                        ['evil.lua'] = 'HACKED = true' } })
  H.callbacks.onSimulationStart()
  H.run(3)
  local carrier
  for _, call in ipairs(loaderCalls(H)) do if call.code:find('^if OverlordCarrier ') then carrier = call.code end end
  assert(carrier and carrier:find('loadstring(' .. string.format('%q', hostile) .. ', "=Scripts/Overlord/overlord_carrier.lua")',
    1, true), 'source passed as one %q literal')
  local source
  for _, load in ipairs(W.loads) do if load.name == '=Scripts/Overlord/overlord_carrier.lua' then source = load.source end end
  eq(source, hostile, 'mission receives the exact bytes')
  local posts = H.on('/carrier/exchange')
  eq(#posts, 2, 'hostile-byte companion bridges')
  eq(posts[1].body.marker, 'quoted')
  eq(rawget(W.env, 'OverlordJTAC'), nil, 'comment text is not a module')
  assert(logged(H, '[jtac]: auto-load: companion did not define its module'))
  eq(#H.on('/dcs/exchange'), 0)
  for _, path in ipairs(H.paths) do
    assert(path == H.dir .. '/Scripts/Overlord/overlord_carrier.lua' or path == H.dir .. '/Scripts/Overlord/overlord_jtac.lua',
      'only fixed companion paths opened: ' .. path)
  end
  eq(rawget(W.env, 'HACKED'), nil, 'unlisted file never loaded')
  eq(rawget(_G, 'HACKED'), nil, 'nothing ran in the hook')
  eq(#W.forbidden, 0, 'os never touched')
  H.cleanup()
end)

check('a missing, oversized, precompiled, broken or failing companion only disables its own feature', function()
  local cases = {
    { nil, 'auto-load: Scripts/Overlord/overlord_jtac.lua not readable' },
    { ('-'):rep(1048577), 'auto-load: Scripts/Overlord/overlord_jtac.lua too large' },
    { '', 'auto-load: Scripts/Overlord/overlord_jtac.lua read failed' },
    { string.dump(function() OverlordJTAC = { bridge = function() return '{}' end } end), 'precompiled chunk refused' },
    { 'OverlordJTAC = (', 'auto-load: companion does not compile' },
    { 'error("boom")', 'auto-load: companion raised an error' },
  }
  for _, case in ipairs(cases) do
    local companions = realCompanions()
    companions['overlord_jtac.lua'] = case[1]
    local W = newMission({ bare = true, config = false })
    local H = newHook(W, { features = '{ "jtac", "carriers" }', extra = 'autoLoad = true', companions = companions })
    H.callbacks.onSimulationStart()
    H.run(4)
    local what = case[2]
    assert(logged(H, '[jtac]: ' .. what) or logged(H, what), 'logged: ' .. what)
    eq(#H.on('/dcs/exchange'), 0, 'jtac disabled: ' .. what)
    eq(#H.on('/carrier/exchange'), 3, 'carriers unaffected: ' .. what)
    eq(rawget(W.env, 'OverlordJTAC'), nil, 'no module from a refused file: ' .. what)
    H.run(3)
    eq(opened(H, 'overlord_jtac.lua'), 1, 'one attempt per epoch: ' .. what)
    H.callbacks.onSimulationStop()
    H.callbacks.onSimulationStart()
    H.run(2)
    eq(opened(H, 'overlord_jtac.lua'), 2, 'retried next epoch: ' .. what)
    eq(#W.forbidden, 0)
    H.cleanup()
  end
end)

check('autoLoad must be a boolean', function()
  local H = newHook(newMission({ bare = true, config = false }), { extra = 'autoLoad = "yes"', companions = realCompanions() })
  H.callbacks.onSimulationStart()
  H.run(2)
  eq(#H.calls, 0, 'hook disabled')
  assert(logged(H, 'autoLoad must be true or false'))
  H.cleanup()
end)

check('files are read without seek and unsupported bounded reads fail closed', function()
  local W = newMission({ bare = true, config = false })
  local H = newHook(W, { features = '{ "jtac", "airfields" }', extra = 'autoLoad = true', companions = realCompanions() })
  writeFile(H.dir .. '/dcs/Mods/terrains/Caucasus/Radio.lua', RADIO_FILE)
  H.callbacks.onSimulationStart()
  H.run(3)
  eq(table.concat(H.reads, ','), '4194305,1048577,1048577', 'Radio.lua then both companions, limit + 1 bytes each')
  eq(count(field(H.on('/airfield/exchange')[1].body, '21').radios), 4, 'native radios through the seekless object')
  eq(#H.on('/dcs/exchange'), 2, 'auto-loaded jtac through the seekless object')
  H.cleanup()
  local companions = realCompanions()
  companions['overlord_jtac.lua'] = ('-'):rep(1048587)
  H = newHook(newMission({ bare = true, config = false }), { features = '{ "jtac" }', extra = 'autoLoad = true',
                                                             companions = companions })
  H.callbacks.onSimulationStart()
  H.run(2)
  eq(table.concat(H.reads, ','), '1048577', 'oversized file: one bounded read, never a whole-file read')
  assert(logged(H, 'overlord_jtac.lua too large'))
  H.cleanup()
  H = newHook(newMission({ mutate = quiet, config = false }), { noNumericRead = true })
  writeFile(H.dir .. '/dcs/Mods/terrains/Caucasus/Radio.lua', RADIO_FILE)
  H.callbacks.onSimulationStart()
  H.run(1)
  eq(table.concat(H.reads, ','), '4194305', 'never fall back to an unbounded whole-file read')
  eq(count(field(H.on('/airfield/exchange')[1].body, '21').radios), 0, 'unsupported reads cannot publish radios')
  H.cleanup()
end)

check('mission scripts live in the server state: state = "mission" is refused, every call targets server', function()
  local W = newMission({ bare = true, config = false })
  local H = newHook(W, { state = 'mission', extra = 'autoLoad = true', companions = realCompanions() })
  H.callbacks.onSimulationStart()
  H.run(2)
  eq(#H.calls, 0, 'hook disabled')
  assert(logged(H, 'state must be "server"'))
  eq(H.env.net.dostring_in('mission', 'return type(world)'), 'nil', 'mission state has no world')
  eq(H.env.net.dostring_in('server', 'return type(world)'), 'table', 'server state runs mission scripts')
  H.cleanup()
  H = newHook(W, { features = ALL, extra = 'autoLoad = true', companions = realCompanions() })
  H.callbacks.onSimulationStart()
  H.run(3)
  eq(#loaderCalls(H), 3, 'every companion auto-loaded')
  for _, call in ipairs(H.calls) do eq(call.state, 'server', 'exchange and loader target the server state') end
  eq(#H.on('/dcs/exchange'), 2)
  H.cleanup()
end)

check('an internal hook error is logged by kind only, never with values', function()
  local W = newMission({ others = true })
  local H = newHook(W, { features = '{ "jtac", "carriers" }' })
  H.callbacks.onSimulationStart()
  H.run(1)
  H.env.net.dostring_in = nil
  H.run(2)
  assert(logged(H, "[jtac]: internal error: attempt to call field 'dostring_in' (a nil value)"), 'classified')
  assert(logged(H, "[carriers]: internal error: attempt to call field 'dostring_in' (a nil value)"), 'per feature')
  for _, line in ipairs(H.logs) do assert(not line:find('secret'), 'token logged') end
  H.cleanup()
end)

print(string.format('%d passed, %d failed (offline doubles; not a real DCS acceptance test)', passed, failures))
if failures > 0 then os.exit(1) end
