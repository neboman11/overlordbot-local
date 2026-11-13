--[[
Offline harness for dcs/overlord_carrier.lua and the carriers feature of
dcs/overlord_hook.lua.

Runs the real carrier companion (plus the real JTAC companion for multi-feature
hook cases) and the real hook in strict sandboxed Lua environments with test
doubles for the DCS mission API, net.dostring_in, LuaSocket and the brain.  It
proves registry extraction, identity/watch/lease handling, lifecycle events and
feature isolation in plain Lua; it is NOT acceptance inside a real DCS mission
(event timing, Supercarrier deck behaviour and hook permissions must be checked
on a DCS server).

Usage (from the repository root):  lua tests/carrier_harness.lua
]]

local ROOT = (arg and arg[0] or 'tests/carrier_harness.lua'):gsub('[^/\\]*$', '') .. '../'
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

-- DCS mission scripting doubles -------------------------------------------------

local EVENT = { S_EVENT_TAKEOFF = 3, S_EVENT_LAND = 4, S_EVENT_CRASH = 5, S_EVENT_EJECTION = 6, S_EVENT_DEAD = 8,
                S_EVENT_PILOT_DEAD = 9, S_EVENT_MISSION_END = 12, S_EVENT_BIRTH = 15,
                S_EVENT_PLAYER_ENTER_UNIT = 20, S_EVENT_PLAYER_LEAVE_UNIT = 21, S_EVENT_KILL = 28,
                S_EVENT_UNIT_LOST = 30, S_EVENT_RUNWAY_TAKEOFF = 54, S_EVENT_RUNWAY_TOUCH = 55 }

local CVN71, CVN74 = 'CVN-71 Theodore Roosevelt', 'CVN-74 John C. Stennis'
local LEAD, WING, AI = 'Hornet 1-1', 'Hornet 1-2', 'AI Hawkeye'

local function newMission(opts)
  opts = opts or {}
  local W = { now = 0, abs = 8 * 3600, scheduled = {}, units = {}, handlers = {}, errors = {}, forbidden = {},
              polls = {}, order = {} }
  local Object = {}
  Object.__index = Object
  local function poll(self) W.polls[self.name] = (W.polls[self.name] or 0) + 1 end
  function Object:isExist() return self.alive end
  function Object:isActive() return self.active end
  function Object:getLife() return self.alive and 10 or 0 end
  function Object:getID() return self.id end
  function Object:getName() return self.name end
  function Object:getTypeName() return self.type end
  function Object:getCoalition() return self.coalition end
  function Object:getPlayerName() return self.player end
  function Object:getCallsign() return self.callsign end
  function Object:getDesc() return { category = self.category } end
  function Object:hasAttribute(name) return self.attributes[name] == true end
  function Object:getPoint() poll(self); return { x = self.x, y = self.y, z = self.z } end
  function Object:getVelocity() poll(self); return { x = self.vx, y = 0, z = self.vz } end
  function Object:getPosition()
    poll(self)
    return { p = { x = self.x, y = self.y, z = self.z }, x = { x = math.cos(self.heading), y = 0, z = math.sin(self.heading) },
             y = { x = 0, y = 1, z = 0 }, z = { x = -math.sin(self.heading), y = 0, z = math.cos(self.heading) } }
  end
  function Object:inAir() poll(self); return self.air end
  function W.unit(spec)
    local u = setmetatable({ id = spec.id, name = spec.name, type = spec.type, coalition = spec.coalition or 2,
                             category = spec.category or 0, x = spec.x or 0, y = spec.y or 0, z = spec.z or 0,
                             vx = spec.vx or 0, vz = spec.vz or 0, heading = spec.heading or 0, alive = true,
                             active = true, air = spec.air == true, player = spec.player, callsign = spec.callsign,
                             attributes = spec.attributes or {} }, Object)
    W.units[spec.name] = u
    W.order[#W.order + 1] = u
    return u
  end
  function W.place(unit) return { getName = function() return unit.name end, getUnit = function() return unit end } end
  function W.event(id, unit, place)
    for _, handler in ipairs(W.handlers) do handler:onEvent({ id = id, time = W.now, initiator = unit, place = place }) end
  end
  function W.advance(dt)
    local target = W.now + dt
    while W.now < target do
      W.now = math.min(target, W.now + 0.25)
      local due = W.scheduled
      W.scheduled = {}
      for _, item in ipairs(due) do
        if item.at <= W.now then
          local nextAt = item.fn(item.arg, W.now)
          if nextAt then W.scheduled[#W.scheduled + 1] = { fn = item.fn, arg = item.arg, at = nextAt } end
        else
          W.scheduled[#W.scheduled + 1] = item
        end
      end
    end
  end

  -- Ships: authoritative radios on the ship UNIT in Hz; group frequency is MHz and must be ignored.
  W.cvn71 = W.unit({ id = 101, name = CVN71, type = 'CVN_71', category = 3, x = 0, z = 0, vx = 10, heading = 0 })
  W.cvn74 = W.unit({ id = 102, name = CVN74, type = 'Stennis', category = 3, x = 0, z = 2000, vz = 12,
                     heading = math.pi / 2 })
  W.vinson = W.unit({ id = 103, name = 'CVN-70 Vinson', type = 'VINSON', category = 3, x = 9000, z = 0 })
  W.burke = W.unit({ id = 104, name = 'DDG Burke', type = 'USS_Arleigh_Burke_IIa', category = 3, x = 500, z = 500 })
  W.mod = W.unit({ id = 105, name = 'Enterprise Mod', type = 'cvn-65', category = 3, x = 20000, z = 0,
                   attributes = { ['Aircraft Carriers'] = true } })
  W.kuz = W.unit({ id = 201, name = 'Kuznetsov', type = 'KUZNECOW', coalition = 1, category = 3, x = -50000, z = 0 })
  W.lead = W.unit({ id = 301, name = LEAD, type = 'FA-18C_hornet', x = 10, z = 2, vx = 10, player = 'Maverick',
                    callsign = 'Enfield11' })
  W.wing = W.unit({ id = 302, name = WING, type = 'FA-18C_hornet', x = 30, z = 2, vx = 10, player = 'Iceman' })
  W.ai = W.unit({ id = 401, name = AI, type = 'E-2C', x = 0, z = 2010, vz = 12 })

  W.mission = {
    date = { Year = 2024, Month = 12, Day = 31 },
    coalition = {
      blue = { country = { {
        id = 2, name = 'USA',
        ship = { group = { {
          name = 'CSG', frequency = 127.5,
          units = {
            { unitId = 101, name = CVN71, type = 'CVN_71', frequency = 305000000 },
            { unitId = 102, name = CVN74, type = 'Stennis', frequency = 127500000, modulation = 1 },
            { unitId = 103, name = 'CVN-70 Vinson', type = 'VINSON', frequency = 127.5 },
            { unitId = 104, name = 'DDG Burke', type = 'USS_Arleigh_Burke_IIa', frequency = 260000000 },
            { unitId = 105, name = 'Enterprise Mod', type = 'cvn-65', frequency = 262000000 },
          },
        } } },
        plane = { group = {
          { name = 'Hornet 1', frequency = 305, route = { points = { { type = 'TakeOffParking', linkUnit = 101 } } },
            units = {
              { unitId = 301, name = LEAD, skill = 'Client', onboard_num = '007',
                callsign = { 1, 1, 1, name = 'Enfield11' } },
              { unitId = 302, name = WING, skill = 'Client', onboard_num = '310' },
            } },
          { name = 'Wedge', route = { points = { { type = 'TakeOffParking', linkUnit = 102 } } },
            units = { { unitId = 401, name = AI, skill = 'High', onboard_num = '600' } } },
        } },
      } } },
      red = { country = { {
        id = 0, name = 'Russia',
        ship = { group = { { name = 'Northern', units = { { unitId = 201, name = 'Kuznetsov', type = 'KUZNECOW' } } } } },
      } } },
    },
  }

  local allowedNil = { OverlordCarrier = true, OverlordCarrierConfig = true, OverlordJTAC = true,
                       OverlordJTACConfig = true }
  local base = {
    math = math, string = string, table = table, ipairs = ipairs, pairs = pairs, type = type,
    tostring = tostring, tonumber = tonumber, pcall = pcall, setmetatable = setmetatable, next = next,
    select = select, error = error,
    env = { mission = W.mission, info = function() end, error = function(m) W.errors[#W.errors + 1] = m end },
    timer = {
      getTime = function() return W.now end,
      getAbsTime = function() return W.abs end,
      scheduleFunction = function(fn, a, at) W.scheduled[#W.scheduled + 1] = { fn = fn, arg = a, at = at } end,
    },
    Unit = { getByName = function(n) return W.units[n] end },
    Group = { getByName = function() return nil end },
    StaticObject = { getByName = function() return nil end },
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
    coord = { LOtoLL = function(p) return 42 + p.x / 111000, 41 + p.z / (111000 * math.cos(math.rad(42))), p.y end },
    atmosphere = { getTemperatureAndPressure = function(p)
      assert(p.y == 0, 'sea-level pressure query')
      return 288.15, 101591.67
    end },
    world = { event = EVENT, addEventHandler = function(h) W.handlers[#W.handlers + 1] = h end },
    net = { lua2json = encode, json2lua = decode },
  }
  W.env = setmetatable(base, { __index = function(_, k)
    if allowedNil[k] then return nil end
    W.forbidden[#W.forbidden + 1] = k
    error('mission script touched forbidden global ' .. tostring(k), 2)
  end })
  if opts.config ~= false then
    W.env.OverlordCarrierConfig = opts.config or {
      carriers = {
        [CVN71] = { recoveryCase = 1, ceilingFt = 5000, visibilityNm = 10 },
        [CVN74] = { recoveryCase = 3, altimeterInHg = 29.92 },
        ['CVN-70 Vinson'] = { recoveryCase = 2, frequency = 127500000 },
        Ghost = { recoveryCase = 1 },
      },
    }
  end
  if opts.mutate then opts.mutate(W) end
  if opts.jtac then
    W.env.OverlordJTACConfig = { tasks = {} }
    compile(readFile(ROOT .. 'dcs/overlord_jtac.lua'), '@overlord_jtac.lua', W.env)()
  end
  compile(readFile(ROOT .. 'dcs/overlord_carrier.lua'), '@overlord_carrier.lua', W.env)()
  return W
end

local function bridge(W, epoch, inbound)
  local raw = inbound == nil and '' or type(inbound) == 'string' and inbound or encode(inbound)
  local out = W.env.OverlordCarrier.bridge(epoch, raw)
  W.last = out and decode(out)
  return W.last
end

local function accept(W, watch, overrides)
  local message = { version = 1, accepted = true, epoch = W.last.epoch, seq = W.last.seq, watch = watch or {} }
  for k, v in pairs(overrides or {}) do message[k] = v end
  return message
end

local function find(list, key, value)
  for _, item in ipairs(list or {}) do if item[key] == value then return item end end
end

local function player(s, id) return find(s.players, 'unit_id', id) end
local function aircraft(s, id) return find(s.aircraft, 'unit_id', id) end
local function carrier(s, id) return find(s.carriers, 'id', id) end
local function count(list) local n = 0 for _ in pairs(list or {}) do n = n + 1 end return n end

local function watchOf(s, id, carrierId)
  return { unit_id = id, incarnation = player(s, id).incarnation, carrier_id = carrierId }
end

local function kinds(record)
  local out = {}
  for _, event in ipairs(record and record.events or {}) do
    out[#out + 1] = event.type .. (event.carrier_id and ('@' .. event.carrier_id) or '')
  end
  return table.concat(out, ',')
end

-- Enrol unit `id` with carrier `carrierId` through the real handshake; returns the snapshot carrying A.
local function enrol(W, epoch, id, carrierId)
  local s = W.last and W.last.epoch == epoch and W.last or bridge(W, epoch)
  return bridge(W, epoch, accept(W, { watchOf(s, id, carrierId) }))
end

-- Mission companion ---------------------------------------------------------------

check('registry reads every carrier ship unit radio in Hz from env.mission without guessing', function()
  local W = newMission()
  local s = bridge(W, 'e1')
  eq(count(s.carriers), 5, 'carriers (destroyer excluded, invalid radios kept)')
  local ids = {}
  for _, c in ipairs(s.carriers) do ids[#ids + 1] = c.id end
  eq(table.concat(ids, ','), '101,102,103,105,201', 'all carriers in unit id order')
  local a, b, v, mod, kuz = carrier(s, '101'), carrier(s, '102'), carrier(s, '103'), carrier(s, '105'), carrier(s, '201')
  eq(a.frequency_hz, 305000000, 'Hz preserved')
  eq(a.modulation, 0, 'omitted modulation is native AM')
  eq(a.type, 'CVN_71')
  eq(a.coalition, 2)
  eq(a.alive, true)
  eq(a.recovery_case, 1)
  eq(a.ceiling_ft, 5000)
  eq(a.visibility_nm, 10)
  eq(a.altimeter_inhg, 30, 'altimeter from DCS sea-level pressure')
  eq(a.deck_angle_deg, 9.1359)
  eq(a.deck_height_m, 20.1494)
  eq(a.velocity.x, 10)
  assert(math.abs(a.heading_deg) < 0.01 or math.abs(a.heading_deg - 360) < 0.01, 'north heading')
  assert(math.abs(a.lat - 42) < 1e-6 and math.abs(a.lon - 41) < 1e-6, 'lat/lon')
  eq(b.frequency_hz, 127500000)
  eq(b.modulation, 1, 'FM preserved')
  eq(b.recovery_case, 3)
  eq(b.altimeter_inhg, 29.92, 'config altimeter override')
  eq(b.ceiling_ft, nil, 'no ceiling invented')
  assert(math.abs(b.heading_deg - 90) < 0.5, 'true heading east, got ' .. tostring(b.heading_deg))
  eq(v.frequency_hz, nil, 'MHz-looking value is not converted')
  eq(v.recovery_case, nil, 'config with a frequency key is rejected')
  eq(v.deck_angle_deg, 9.1359)
  eq(mod.frequency_hz, 262000000, 'native Aircraft Carriers attribute discovers unknown type')
  eq(mod.deck_angle_deg, nil, 'unknown type gets no deck geometry')
  eq(kuz.coalition, 1)
  eq(kuz.frequency_hz, nil, 'missing frequency reported unavailable')
  eq(kuz.deck_angle_deg, nil, 'Kuznetsov geometry not invented')
  assert(s.error:find('CVN-70 Vinson: frequency not integer Hz', 1, true), s.error)
  assert(s.error:find('Kuznetsov: no frequency', 1, true), s.error)
  assert(s.error:find('config CVN-70 Vinson.frequency rejected', 1, true), 'frequency in config rejected')
  assert(s.error:find('unknown carrier Ghost', 1, true), 'config cannot add carriers')
  eq(s.seq, 1)
  eq(#W.forbidden, 0, 'forbidden globals')
end)

check('dead or inactive carriers are reported without position', function()
  local W = newMission()
  W.cvn71.alive = false
  W.cvn74.active = false
  local s = bridge(W, 'e1')
  eq(carrier(s, '101').alive, false)
  eq(carrier(s, '101').point, nil, 'no fabricated position')
  eq(carrier(s, '101').frequency_hz, 305000000, 'radio identity kept for routing')
  eq(carrier(s, '102').alive, false, 'late-activated carrier not live')
end)

check('human catalog lists only player-occupied aircraft and never polls them', function()
  local W = newMission()
  local s = bridge(W, 'e1')
  eq(count(s.players), 2, 'two humans, AI excluded')
  local lead = player(s, '301')
  eq(lead.unit_name, LEAD)
  eq(lead.player_name, 'Maverick')
  eq(lead.callsign, 'Enfield11')
  eq(lead.modex, '007', 'leading-zero modex preserved')
  eq(lead.coalition, 2)
  eq(lead.human, true)
  eq(player(s, '302').modex, '310')
  eq(player(s, '302').callsign, '', 'no callsign invented')
  eq(player(s, '401'), nil, 'AI not catalogued')
  eq(count(s.aircraft), 0, 'no aircraft state without watch')
  local again = bridge(W, 'e1')
  eq(player(again, '301').incarnation, lead.incarnation, 'stable incarnation while occupancy unchanged')
  W.advance(5)
  for _, name in ipairs({ LEAD, WING, AI, 'DDG Burke' }) do eq(W.polls[name], nil, name .. ' polled') end
end)

check('watch applies once from the latest accepted response; AI and unregistered never polled', function()
  local W = newMission()
  local s1 = bridge(W, 'e1')
  local stale = accept(W, {})                       -- valid for seq 1 only
  local s2 = bridge(W, 'e1', accept(W, { watchOf(s1, '301', '101') }))
  local a = aircraft(s2, '301')
  assert(a, 'watched aircraft reported in the next snapshot')
  eq(a.carrier_id, '101')
  eq(a.incarnation, player(s2, '301').incarnation)
  eq(a.in_air, false)
  eq(a.point.x, 10)
  eq(a.velocity.x, 10)
  eq(count(s2.aircraft), 1)
  local s3 = bridge(W, 'e1', stale)
  assert(aircraft(s3, '301'), 'stale response cannot clear the watch')
  local old = accept(W, { watchOf(s3, '301', '101') })
  local s4 = bridge(W, 'e1', accept(W, {}))
  eq(aircraft(s4, '301'), nil, 'empty accepted watch releases')
  local s5 = bridge(W, 'e1', old)
  eq(aircraft(s5, '301'), nil, 'replayed response cannot resurrect membership')
  local good = accept(W, { watchOf(s5, '301', '101') })
  for _, bad in ipairs({
    accept(W, { watchOf(s5, '301', '101') }, { epoch = 'e0' }),
    accept(W, { watchOf(s5, '301', '101') }, { accepted = false }),
    accept(W, { watchOf(s5, '301', '101') }, { version = 2 }),
    accept(W, { { unit_id = '301', incarnation = 'forged', carrier_id = '101' } }),
    accept(W, { watchOf(s5, '301', '999') }),
    accept(W, { { unit_id = '401', incarnation = '401:1', carrier_id = '101' } }),
    'not json', string.rep('x', 70000),
  }) do
    if type(bad) == 'table' then bad.seq = W.last.seq end
    local s = bridge(W, 'e1', bad)
    eq(count(s.aircraft), 0, 'invalid response or watch entry ignored')
  end
  local missing = accept(W, nil)
  missing.watch = nil
  eq(count(bridge(W, 'e1', missing).aircraft), 0, 'accepted response without watch list ignored')
  eq(count(bridge(W, 'e1', good).aircraft), 0, 'good body for an older seq is not reused')
  local s6 = bridge(W, 'e1', accept(W, { watchOf(W.last, '301', '101') }))
  assert(aircraft(s6, '301'), 'fresh watch works')
  local body = encode(accept(W, { watchOf(s6, '301', '101') }))
  bridge(W, 'e1', body)
  local twice = bridge(W, 'e1', body)
  assert(aircraft(twice, '301'), 'repeated body neither renews nor clears')
  W.advance(10)
  eq(W.polls[WING], nil, 'unregistered human never polled')
  eq(W.polls[AI], nil, 'AI never polled')
  assert((W.polls[LEAD] or 0) > 0, 'watched human polled')
end)

check('watch lease expires without fresh accepted responses and polling stops', function()
  local W = newMission()
  enrol(W, 'e1', '301', '101')
  local renewal = accept(W, { watchOf(W.last, '301', '101') })
  W.advance(10)
  local s = bridge(W, 'e1')
  assert(aircraft(s, '301'), 'lease valid at 10 s')
  for _ = 1, 6 do
    W.advance(1)
    s = bridge(W, 'e1', renewal)                     -- old success is not a fresh brain answer
  end
  eq(aircraft(s, '301'), nil, 'watch expired after lease')
  local polls = W.polls[LEAD]
  W.advance(5)
  bridge(W, 'e1')
  eq(W.polls[LEAD], polls, 'no polling after lease expiry')
end)

check('slot change, disconnect and respawn create a new incarnation that never inherits the watch', function()
  local W = newMission()
  local s = enrol(W, 'e1', '301', '101')
  local first = player(s, '301').incarnation
  local oldWatch = { watchOf(s, '301', '101') }
  W.lead.player = 'Goose'
  s = bridge(W, 'e1', accept(W, oldWatch))
  local second = player(s, '301').incarnation
  assert(second ~= first, 'player change renews incarnation')
  eq(player(s, '301').player_name, 'Goose')
  eq(aircraft(s, '301'), nil, 'old-incarnation watch rejected')
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '101') }))
  assert(aircraft(s, '301'), 'new incarnation watchable')
  W.event(EVENT.S_EVENT_BIRTH, W.lead, W.place(W.cvn71))
  s = bridge(W, 'e1', accept(W, { { unit_id = '301', incarnation = second, carrier_id = '101' } }))
  local third = player(s, '301').incarnation
  assert(third ~= second, 'respawn renews incarnation')
  eq(aircraft(s, '301'), nil, 'respawned aircraft not tracked')
  W.lead.player = nil
  s = bridge(W, 'e1')
  eq(player(s, '301'), nil, 'vacated slot leaves catalog')
  W.lead.player = 'Goose'
  s = bridge(W, 'e1')
  assert(player(s, '301').incarnation ~= third, 'reoccupied slot is a new incarnation')
end)

check('land/takeoff use the event deck on two moving carriers: bolter, dedupe, ship-relative on_deck', function()
  local W = newMission()
  W.lead.air, W.lead.x, W.lead.z = true, 50, 100       -- physically nearer CVN-71
  enrol(W, 'e1', '301', '102')
  W.event(EVENT.S_EVENT_RUNWAY_TOUCH, W.lead, W.place(W.cvn74))
  W.event(EVENT.S_EVENT_RUNWAY_TAKEOFF, W.lead, W.place(W.cvn74))
  W.event(EVENT.S_EVENT_TAKEOFF, W.lead, W.place(W.cvn74))
  local s = bridge(W, 'e1')
  eq(kinds(aircraft(s, '301')), 'land@102,takeoff@102', 'contact then bolter, duplicate takeoff removed')
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '102') }))
  eq(kinds(aircraft(s, '301')), '', 'acknowledged events not resent')
  W.event(EVENT.S_EVENT_RUNWAY_TOUCH, W.lead, W.place(W.cvn74))
  W.event(EVENT.S_EVENT_LAND, W.lead, W.place(W.cvn74))
  W.lead.air, W.lead.vx, W.lead.vz = false, 0, 12       -- trapped: moving with CVN-74
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '102') }))
  eq(kinds(aircraft(s, '301')), 'land@102', 'single contact event')
  eq(aircraft(s, '301').on_deck, false, 'not yet confirmed')
  s = bridge(W, 'e1')
  eq(kinds(aircraft(s, '301')), 'land@102', 'unacknowledged event resent with same id')
  W.advance(3.5)
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '102') }))
  eq(aircraft(s, '301').on_deck, true, 'stopped relative to the moving deck')
  eq(aircraft(s, '301').velocity.z, 12, 'absolute velocity reported')
  W.lead.vz = 0                                       -- stationary in sea frame: sliding on deck
  W.advance(4)
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '102') }))
  eq(aircraft(s, '301').on_deck, false, 'absolute standstill is not ship-relative stop')
  W.lead.vx, W.lead.vz = 10, 0                        -- now matches CVN-71 velocity, but deck is CVN-74
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '101') }))
  W.advance(4)
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '101') }))
  eq(aircraft(s, '301').carrier_id, '101')
  eq(aircraft(s, '301').on_deck, false, 'never on the deck of a carrier it did not land on')
end)

check('mission spawn link and birth place give deck identity without proximity', function()
  local W = newMission()
  W.lead.x, W.lead.z, W.lead.vx = 0, 2000, 10          -- parked where CVN-74 is, moving like CVN-71
  enrol(W, 'e1', '301', '102')
  W.advance(4)
  local s = bridge(W, 'e1', accept(W, { watchOf(W.last, '301', '102') }))
  eq(aircraft(s, '301').on_deck, false, 'position near CVN-74 is not deck identity')
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '101') }))
  W.advance(4)
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '101') }))
  eq(aircraft(s, '301').on_deck, true, 'mission linkUnit spawn deck')
  W.event(EVENT.S_EVENT_BIRTH, W.lead, W.place(W.cvn74))
  W.lead.vx, W.lead.vz = 0, 12
  s = bridge(W, 'e1')
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '102') }))
  W.advance(4)
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '102') }))
  eq(aircraft(s, '301').on_deck, true, 'birth place overrides slot link')
end)

check('AI and unregistered lifecycle events leave no buffers and trigger no polling', function()
  local W = newMission()
  bridge(W, 'e1')
  W.event(EVENT.S_EVENT_LAND, W.ai, W.place(W.cvn74))
  W.event(EVENT.S_EVENT_TAKEOFF, W.ai, W.place(W.cvn74))
  W.event(EVENT.S_EVENT_DEAD, W.ai)
  W.event(EVENT.S_EVENT_RUNWAY_TOUCH, W.wing, W.place(W.cvn71))
  W.event(EVENT.S_EVENT_RUNWAY_TAKEOFF, W.wing, W.place(W.cvn71))
  local s = enrol(W, 'e1', '302', '101')
  eq(kinds(aircraft(s, '302')), '', 'pre-registration events are not replayed')
  eq(aircraft(s, '401'), nil)
  eq(W.polls[AI], nil, 'AI never polled')
  eq(W.polls[LEAD], nil, 'other human never polled')
end)

check('dead, eject and disconnect send one final event then release the identity', function()
  for _, case in ipairs({ { EVENT.S_EVENT_DEAD, 'dead', true }, { EVENT.S_EVENT_EJECTION, 'eject', false },
                          { EVENT.S_EVENT_PLAYER_LEAVE_UNIT, 'disconnect', true } }) do
    local W = newMission()
    local s = enrol(W, 'e1', '301', '101')
    local incarnation = player(s, '301').incarnation
    if case[1] == EVENT.S_EVENT_PLAYER_LEAVE_UNIT then W.lead.player = nil end
    if case[1] == EVENT.S_EVENT_DEAD then W.lead.alive = false end
    W.event(case[1], W.lead)
    W.event(case[1], W.lead)
    s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '101') }))
    eq(player(s, '301').incarnation, incarnation, case[2] .. ': identity kept for the final snapshot')
    eq(kinds(aircraft(s, '301')), case[2] .. '@101', case[2] .. ': one terminal event')
    eq(aircraft(s, '301').point.x, 10, case[2] .. ': last real sample')
    s = bridge(W, 'e1', accept(W, { { unit_id = '301', incarnation = incarnation, carrier_id = '101' } }))
    eq(aircraft(s, '301'), nil, case[2] .. ': released')
    if case[3] then
      eq(player(s, '301'), nil, case[2] .. ': gone from catalog')
    else
      assert(player(s, '301').incarnation ~= incarnation, case[2] .. ': still-listed unit gets a new incarnation')
    end
  end
end)

check('epoch change resets sequence and watch; mission end stops the bridge', function()
  local W = newMission()
  local s = enrol(W, 'e1', '301', '101')
  local old = player(s, '301').incarnation
  local reply = accept(W, { watchOf(s, '301', '101') })
  s = bridge(W, 'e2', reply)
  eq(s.seq, 1, 'sequence restarted')
  eq(count(s.aircraft), 0, 'previous epoch watch not applied')
  assert(player(s, '301').incarnation ~= old, 'incarnations not reused across epochs')
  eq(bridge(W, 'bad epoch!'), nil, 'epoch validated')
  W.event(EVENT.S_EVENT_MISSION_END)
  eq(W.env.OverlordCarrier.bridge('e2', ''), nil, 'stopped')
end)

check('mission date advances when absolute time passes midnight', function()
  local W = newMission()
  W.abs = 86400 + 3600
  local s = bridge(W, 'e1')
  eq(s.abs_time, 3600)
  eq(s.date.year, 2025)
  eq(s.date.month, 1)
  eq(s.date.day, 1)
  W.mission.date = { Year = 2024, Month = 2, Day = 28 }
  W.abs = 86400 + 10
  s = bridge(W, 'e1')
  eq(s.date.month, 2)
  eq(s.date.day, 29, 'leap day')
end)

check('carrier script runs without any operational config', function()
  local W = newMission({ config = false })
  local s = bridge(W, 'e1')
  eq(carrier(s, '101').recovery_case, nil, 'CASE unavailable without config')
  eq(carrier(s, '101').frequency_hz, 305000000)
  assert(not (s.error or ''):find('config', 1, true), 'no config errors')
end)

local ZWSP, KUZ_RU = 'Bob\226\128\139', '\208\154\209\131\208\183\208\189\208\181\209\134\208\190\208\178'

local function hostileIdentities(W)
  local blue = W.mission.coalition.blue.country[1]
  local function slot(id, name, modex, playerName)
    W.unit({ id = id, name = name, type = 'FA-18C_hornet', x = 40, z = 2, vx = 10, player = playerName })
    blue.plane.group[#blue.plane.group + 1] = { name = name, route = { points = { { linkUnit = 101 } } },
      units = { { unitId = id, name = name, skill = 'Client', onboard_num = modex } } }
  end
  slot(311, 'Hornet 2-1', 'A-201', 'Jester')          -- invalid optional modex: omitted, player kept
  slot(312, 'Hornet 2-2', 'NAVY201X9', 'Hollywood')   -- over 8 characters
  slot(313, 'Hornet 2-3', '205', '   ')               -- whitespace-only player name
  slot(314, 'Hornet 2-4', '206', 'bad\255utf8')       -- invalid UTF-8
  slot(315, 'Hornet 2-5', '207', ZWSP)                -- valid UTF-8: sent unchanged, brain decides
  W.unit({ id = 202, name = KUZ_RU, type = 'KUZNECOW', coalition = 1, category = 3, x = -60000, z = 0 })
  local red = W.mission.coalition.red.country[1].ship.group[1].units
  red[#red + 1] = { unitId = 202, name = KUZ_RU, type = 'KUZNECOW' }
end

check('invalid identities are omitted per entry with ASCII diagnostics; valid humans and ships still flow', function()
  local W = newMission({ mutate = hostileIdentities, config = false })
  local s = bridge(W, 'e1')
  eq(player(s, '301').modex, '007', 'valid modex untouched')
  eq(player(s, '311').player_name, 'Jester', 'player kept when modex is invalid')
  eq(player(s, '311').modex, '', 'invalid modex omitted, not invented')
  eq(player(s, '312').modex, '', 'over-long modex omitted')
  eq(player(s, '313'), nil, 'whitespace-only identity omitted')
  eq(player(s, '314'), nil, 'invalid UTF-8 identity omitted')
  eq(player(s, '315').player_name, ZWSP, 'valid UTF-8 identity sent byte-exact')
  eq(player(s, '302').player_name, 'Iceman', 'other humans unaffected')
  eq(carrier(s, '202').name, KUZ_RU, 'non-ASCII carrier name preserved')
  eq(carrier(s, '101').frequency_hz, 305000000, 'other ships unaffected')
  assert(s.error:find('2 slot modex omitted (not 1-8 letters/digits): Hornet 2-1, Hornet 2-2', 1, true), s.error)
  assert(s.error:find('human identity omitted', 1, true), s.error)
  assert(s.error:find('????????????????: no frequency', 1, true), 'non-ASCII name labelled: ' .. s.error)
  assert(not s.error:find('[\128-\255%c]'), 'diagnostics are printable ASCII')
  assert(#s.error <= 240)
  s = bridge(W, 'e1', accept(W, { watchOf(s, '311', '101') }))
  assert(aircraft(s, '311'), 'aircraft with omitted modex is still watchable')
end)

check('out-of-range samples drop only the affected record, never clamped or zeroed', function()
  local W = newMission()
  local s = bridge(W, 'e1')
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '101'), watchOf(s, '302', '101') }))
  eq(count(s.aircraft), 2)
  W.lead.vx = 2500
  W.cvn74.y = 150000
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '101'), watchOf(s, '302', '101') }))
  eq(aircraft(s, '301'), nil, 'invalid velocity sample skipped')
  assert(aircraft(s, '302'), 'other watched aircraft still reported')
  eq(carrier(s, '102').alive, false, 'carrier with impossible altitude reported not live')
  eq(carrier(s, '102').point, nil, 'no clamped position')
  eq(carrier(s, '101').alive, true, 'other carrier unaffected')
  W.lead.vx = 10
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '101'), watchOf(s, '302', '101') }))
  eq(aircraft(s, '301').velocity.x, 10, 'valid sample reported again')
end)

check('birth before the player name is set still resets deck identity to the slot link', function()
  local W = newMission()
  bridge(W, 'e1')
  W.event(EVENT.S_EVENT_TAKEOFF, W.lead, W.place(W.cvn71))   -- previous occupant left the deck
  W.lead.player = nil
  bridge(W, 'e1')
  W.lead.x, W.lead.z, W.lead.vx, W.lead.air = 0, 2000, 10, false
  W.event(EVENT.S_EVENT_BIRTH, W.lead)                        -- new client, getPlayerName still nil
  W.lead.player = 'Viper'
  local s = bridge(W, 'e1')
  eq(player(s, '301').player_name, 'Viper')
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '101') }))
  W.advance(4)
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '101') }))
  eq(aircraft(s, '301').on_deck, true, 'slot link deck after birth reset')
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '102') }))
  W.lead.vx, W.lead.vz = 0, 12
  W.advance(4)
  s = bridge(W, 'e1', accept(W, { watchOf(s, '301', '102') }))
  eq(aircraft(s, '301').on_deck, false, 'not on CVN-74 despite parking at its position at its speed')
  eq(W.polls[AI], nil, 'AI never polled')
end)

check('missing mission date is reported, never fabricated', function()
  local W = newMission()
  W.mission.date = nil
  local s = bridge(W, 'e1')
  eq(s.date, nil, 'no invented date')
  assert(s.error:find('mission date unavailable in env.mission', 1, true), s.error)
end)

-- Server hook with both features ------------------------------------------------------

local function newHook(W, features)
  local H = { real = 0, requests = {}, calls = {}, logs = {}, conns = {}, stall = {}, replies = {} }
  local dir = os.tmpname()
  os.remove(dir)
  os.execute('mkdir -p "' .. dir .. '/Config/"')
  local f = assert(io.open(dir .. '/Config/overlord-hook.lua', 'w'))
  f:write('return { host = "127.0.0.1", port = 8081, bridgeToken = "bridge-secret", apiToken = "api-secret",'
    .. ' interval = 1, timeout = 3, state = "mission", features = ' .. features .. ' }')
  f:close()

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
      local chunk = self.response:sub(self.offset + 1, self.offset + math.min(n, 64))
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
    },
    net = { dostring_in = function(state, code)
      H.calls[#H.calls + 1] = { state = state, code = code }
      local ok, result = pcall(compile(code, '=dostring', W.env))
      return tostring(result), ok
    end },
    log = { write = function(_, _, m) H.logs[#H.logs + 1] = m end, ERROR = 1, WARNING = 2, INFO = 3 },
    lfs = { writedir = function() return dir .. '/' end },
    package = { path = '', cpath = '' },
    require = function(name) assert(name == 'socket', 'unexpected module ' .. name); return socket end,
  }, { __index = _G })
  compile(readFile(ROOT .. 'dcs/overlord_hook.lua'), '@overlord_hook.lua', env)()
  function H.run(seconds)
    local steps = math.floor(seconds / 0.0625 + 0.5)   -- binary-exact frame step
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

check('hook runs JTAC and carrier features on fixed routes with shared tokens and own sequences', function()
  local W = newMission({ jtac = true })
  local H = newHook(W, '{ "jtac", "carriers" }')
  H.callbacks.onSimulationStart()
  H.replies['/carrier/exchange'] = function(request)
    local lead = find(request.body.players, 'unit_id', '301')
    return encode({ version = 1, accepted = true, epoch = request.body.epoch, seq = request.body.seq,
                    watch = { { unit_id = '301', incarnation = lead.incarnation, carrier_id = '101' } } })
  end
  H.run(3)
  local jtac, carriers = H.on('/dcs/exchange'), H.on('/carrier/exchange')
  eq(#jtac, 3, 'jtac exchanges')
  eq(#carriers, 3, 'carrier exchanges')
  for _, request in ipairs(H.requests) do
    assert(request.headers:find('\r\nX%-DCS%-Token: bridge%-secret'), 'bridge token')
    assert(request.headers:find('\r\nAuthorization: Bearer api%-secret'), 'api token')
  end
  eq(carriers[1].body.seq, 1)
  eq(jtac[1].body.seq, 1)
  eq(carriers[1].body.epoch, jtac[1].body.epoch, 'one hook epoch')
  eq(count(carriers[1].body.carriers), 5)
  eq(count(carriers[1].body.aircraft), 0)
  eq(find(carriers[2].body.aircraft, 'unit_id', '301').carrier_id, '101', 'carrier watch round trip')
  for _, call in ipairs(H.calls) do
    eq(call.state, 'mission')
    assert(call.code:match('^return OverlordJTAC and OverlordJTAC%.bridge%(".-",".*"%)$')
      or call.code:match('^return OverlordCarrier and OverlordCarrier%.bridge%(".-",".*"%)$'), 'fixed entry point')
  end
  for _, line in ipairs(H.logs) do assert(not line:find('secret'), 'token logged') end
  eq(#W.forbidden, 0)
  H.cleanup()
end)

check('a failing JTAC entry point or stalled JTAC brain does not starve carrier exchanges', function()
  local W = newMission({ jtac = true })
  local H = newHook(W, '{ "jtac", "carriers" }')
  H.callbacks.onSimulationStart()
  local bridgeFn = W.env.OverlordJTAC.bridge
  W.env.OverlordJTAC.bridge = function() error('mission failure') end
  H.run(4)
  eq(#H.on('/dcs/exchange'), 0, 'failing JTAC posts nothing')
  eq(#H.on('/carrier/exchange'), 4, 'carrier unaffected by JTAC failure')
  local logged = false
  for _, line in ipairs(H.logs) do if line:find('[jtac]', 1, true) then logged = true end end
  assert(logged, 'feature-scoped error logged')
  W.env.OverlordJTAC.bridge = bridgeFn
  H.stall['/dcs/exchange'] = true
  local before = #H.on('/carrier/exchange')
  H.run(2.6)
  eq(#H.on('/dcs/exchange'), 1, 'jtac request stuck in flight')
  eq(#H.on('/carrier/exchange') - before, 3, 'carrier keeps its own schedule during JTAC stall')
  H.stall['/dcs/exchange'] = nil
  H.run(3)
  assert(#H.on('/dcs/exchange') >= 2, 'jtac recovers after its deadline')
  H.cleanup()
end)

check('restart resets every feature epoch and hostile carrier bytes stay a string literal', function()
  local W = newMission({ jtac = true })
  local H = newHook(W, '{ "carriers", "jtac" }')
  H.callbacks.onSimulationStart()
  H.replies['/carrier/exchange'] = function() return '"]] ) os.exit(1) --\n\r\0' .. "'" .. ' {"version":1}' end
  H.run(2.1)
  H.replies['/carrier/exchange'] = nil
  local hostile
  for _, call in ipairs(H.calls) do if call.code:find('os.exit', 1, true) then hostile = call.code end end
  assert(hostile and hostile:match('^return OverlordCarrier and OverlordCarrier%.bridge%('), 'payload passed as data')
  eq(#W.forbidden, 0, 'no code from the response ran')
  local epoch = H.on('/carrier/exchange')[1].body.epoch
  H.run(0.5)                                          -- let in-flight exchanges finish
  H.callbacks.onSimulationStop()
  local stopped, calls = #H.requests, #H.calls
  H.run(2)
  eq(#H.requests, stopped, 'no requests while stopped')
  eq(#H.calls, calls, 'no mission calls while stopped')
  H.callbacks.onSimulationStart()
  local mark = #H.requests
  H.run(0.1)
  local fresh = {}
  for i = mark + 1, #H.requests do fresh[H.requests[i].path] = H.requests[i].body end
  assert(fresh['/carrier/exchange'] and fresh['/dcs/exchange'], 'both features restart')
  eq(fresh['/carrier/exchange'].seq, 1, 'carrier sequence restarted')
  eq(fresh['/dcs/exchange'].seq, 1, 'jtac sequence restarted')
  assert(fresh['/carrier/exchange'].epoch ~= epoch, 'new epoch')
  H.cleanup()
end)

check('carriers-only hook never calls the JTAC entry point', function()
  local W = newMission({ jtac = true })
  local H = newHook(W, '{ "carriers" }')
  H.callbacks.onSimulationStart()
  H.run(2.1)
  eq(#H.on('/carrier/exchange'), 3)
  eq(#H.on('/dcs/exchange'), 0)
  for _, call in ipairs(H.calls) do assert(not call.code:find('OverlordJTAC', 1, true), 'jtac called') end
  H.cleanup()
end)

print(string.format('%d passed, %d failed (offline doubles; not a real DCS acceptance test)', passed, failures))
if failures > 0 then os.exit(1) end
