--[[
Offline lifecycle harness for dcs/overlord_jtac.lua and dcs/overlord_hook.lua
(JTAC feature, plus feature-list isolation of the shared hook).

Runs the real companion and hook source in separate Lua environments with test
doubles for the DCS scripting API, net.dostring_in, LuaSocket and the brain.  It
proves state handling, escaping, command lifecycle and cleanup in plain Lua; it
is NOT acceptance inside a real DCS mission (lasers, smoke and hook permissions
there must be checked on a DCS server).

Usage (from the repository root):  lua tests/jtac_harness.lua
]]

local ROOT = (arg and arg[0] or 'tests/jtac_harness.lua'):gsub('[^/\\]*$', '') .. '../'
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

local function newMission()
  local W = { now = 0, scheduled = {}, spots = {}, smoke = {}, illumination = {}, visible = true,
              units = {}, groups = {}, statics = {}, handlers = {}, errors = {}, forbidden = {} }
  local Object = {}
  Object.__index = Object
  function Object:isExist() return self.alive end
  function Object:getLife() return self.alive and 10 or 0 end
  function Object:getPoint() return { x = self.x, y = self.y, z = self.z } end
  function Object:getCoalition() return self.coalition end
  function Object:getName() return self.name end
  function W.unit(name, coalition, x, z)
    local u = setmetatable({ name = name, coalition = coalition, x = x, y = 0, z = z, alive = true }, Object)
    W.units[name] = u
    return u
  end
  function W.group(name, members)
    W.groups[name] = {
      members = members,
      isExist = function() for _, u in ipairs(members) do if u.alive then return true end end return false end,
      getUnits = function()
        local out = {}
        for _, u in ipairs(members) do if u.alive then out[#out + 1] = u end end
        return out
      end,
      getInitialSize = function() return #members end,
    }
  end
  local function newSpot(kind, unit, offset, point, code)
    local spot = { kind = kind, unit = unit, offset = offset, point = point, code = code, destroyed = false }
    function spot:setPoint(p) self.point = p end
    function spot:destroy() self.destroyed = true end
    W.spots[#W.spots + 1] = spot
    return spot
  end
  function W.live()
    local out = {}
    for _, s in ipairs(W.spots) do if not s.destroyed then out[#out + 1] = s end end
    return out
  end
  function W.advance(dt)
    W.now = W.now + dt
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
  function W.kill(unit)
    unit.alive = false
    for _, handler in ipairs(W.handlers) do
      handler:onEvent({ id = 8, initiator = unit })
    end
  end
  -- Mission globals that may legitimately be undefined (the carrier companion is optional).
  local allowedNil = { OverlordJTAC = true, OverlordJTACConfig = true, OverlordCarrier = true }
  local base = {
    math = math, string = string, table = table, ipairs = ipairs, pairs = pairs, type = type,
    tostring = tostring, tonumber = tonumber, pcall = pcall, setmetatable = setmetatable, next = next,
    select = select, error = error,
    env = { info = function() end, error = function(m) W.errors[#W.errors + 1] = m end },
    timer = {
      getTime = function() return W.now end,
      scheduleFunction = function(fn, a, at) W.scheduled[#W.scheduled + 1] = { fn = fn, arg = a, at = at } end,
    },
    Unit = { getByName = function(n) return W.units[n] end },
    Group = { getByName = function(n) return W.groups[n] end },
    StaticObject = { getByName = function(n) return W.statics[n] end },
    Spot = {
      createLaser = function(u, o, p, c) return newSpot('laser', u, o, p, c) end,
      createInfraRed = function(u, o, p) return newSpot('ir', u, o, p) end,
    },
    land = { isVisible = function() return W.visible end, getHeight = function() return 0 end },
    coord = { LOtoLL = function(p) return 42 + p.x / 1e5, 41 + p.z / 1e5, p.y end },
    trigger = { action = {
      smoke = function(p, color) W.smoke[#W.smoke + 1] = { point = p, color = color } end,
      illuminationBomb = function(p, power) W.illumination[#W.illumination + 1] = { point = p, power = power } end,
    } },
    world = { event = { S_EVENT_MISSION_END = 12, S_EVENT_DEAD = 8, S_EVENT_CRASH = 5,
                        S_EVENT_KILL = 28, S_EVENT_UNIT_LOST = 30 },
              addEventHandler = function(h) W.handlers[#W.handlers + 1] = h end },
    net = { lua2json = encode, json2lua = decode },
  }
  -- Strict sandbox: any global outside the DCS mission API (io, os, socket, require...) fails loudly.
  W.env = setmetatable(base, { __index = function(_, k)
    if allowedNil[k] then return nil end
    W.forbidden[#W.forbidden + 1] = k
    error('mission script touched forbidden global ' .. tostring(k), 2)
  end })
  return W
end

local function standardMission(config)
  local W = newMission()
  W.jtac = W.unit('JTAC-1 Reaper', 2, 0, 0)
  W.t1, W.t2, W.t3 = W.unit('T1', 1, 1000, 0), W.unit('T2', 1, 1010, 0), W.unit('T3', 1, 1020, 0)
  W.group('Red Armor Column', { W.t1, W.t2, W.t3 })
  W.env.OverlordJTACConfig = config or {
    leaseSeconds = 15,
    tasks = {
      { id = 1, name = 'armor column', coalition = 'blue', controller = 'JTAC-1 Reaper',
        target = { group = 'Red Armor Column' },
        brief = { type = 2, ip = 'IP Dog', heading = 270, distance = 6.5, elevation = 450,
                  description = 'three tanks', location = 'kilo mike one two', mark = 'laser',
                  friendlies = 'south three kilometers', egress = 'north' },
        laserCode = 1688, marks = { 'laser', 'smoke' }, smokeColor = 'white',
        readback = { 'elevation', 'location' }, attackHeading = { 240, 300 }, requireMark = true },
      { id = 2, name = 'bad', coalition = 'blue', controller = 'JTAC-1 Reaper',
        target = { group = 'Red Armor Column' }, laserCode = 1999 },
    },
  }
  compile(readFile(ROOT .. 'dcs/overlord_jtac.lua'), '@overlord_jtac.lua', W.env)()
  return W
end

local function bridge(W, epoch, inbound)
  -- Model the real request/response handshake, priming a snapshot before the
  -- first authorized command. Explicit seq values remain untouched for replay tests.
  if inbound and inbound.epoch == epoch and inbound.seq == nil then
    if not W.lastSnapshot or W.lastSnapshot.epoch ~= epoch then
      W.lastSnapshot = decode(W.env.OverlordJTAC.bridge(epoch, ''))
    end
    inbound.seq = W.lastSnapshot.seq
  end
  local out = W.env.OverlordJTAC.bridge(epoch, inbound and encode(inbound) or '')
  W.lastSnapshot = out and decode(out)
  return W.lastSnapshot
end

local function command(id, op, extra)
  local c = { id = id, op = op, task = 1, deadline = 1e6 }
  for k, v in pairs(extra or {}) do c[k] = v end
  return c
end

local function reply(epoch, commands, cancel, seq)
  return { version = 1, accepted = true, epoch = epoch, seq = seq,
           commands = commands or {}, cancel = cancel or {} }
end

local function ackFor(snapshot, id)
  for _, ack in ipairs(snapshot.acks or {}) do if ack.id == id then return ack end end
end

-- Mission companion -------------------------------------------------------------

check('config validation keeps only valid tasks and reports errors', function()
  local W = standardMission()
  local s = bridge(W, 'e1')
  eq(#s.tasks, 1, 'tasks')
  eq(s.tasks[1].id, 1)
  eq(s.tasks[1].coalition, 2)
  eq(s.tasks[1].target.total, 3)
  eq(s.tasks[1].target.remaining, 3)
  eq(s.tasks[1].laser_code, 1688)
  eq(s.tasks[1].brief.elevation, 450)
  eq(s.tasks[1].controller.alive, true)
  assert(s.error and s.error:find('task 2'), 'invalid task reported')
  eq(s.seq, 1)
  eq(#W.forbidden, 0, 'forbidden globals')
end)

check('laser on executes Spot.createLaser once and replays its acknowledgement', function()
  local W = standardMission()
  bridge(W, 'e1')
  local s = bridge(W, 'e1', reply('e1', { command('c1', 'laser_on') }))
  eq(ackFor(s, 'c1').status, 'done')
  eq(#W.live(), 1, 'live spots')
  eq(W.live()[1].code, 1688)
  eq(W.live()[1].unit, W.jtac)
  eq(s.tasks[1].laser.on, true)
  eq(s.tasks[1].laser.command, 'c1')
  s = bridge(W, 'e1', reply('e1', { command('c1', 'laser_on') }))
  eq(ackFor(s, 'c1').status, 'done', 'replayed ack')
  eq(#W.spots, 1, 'no second spot on replay')
end)

check('watchdog follows a moving target and drops the spot on target or controller loss', function()
  local W = standardMission()
  bridge(W, 'e1', reply('e1', { command('c1', 'laser_on') }))
  W.t1.x = 1500
  W.advance(1)
  eq(W.live()[1].point.x, 1500, 'spot moved with target')
  W.t1.alive = false
  W.advance(1)
  eq(#W.live(), 0, 'spot destroyed with target')
  local s = bridge(W, 'e1', reply('e1', { command('c2', 'laser_on') }))
  eq(ackFor(s, 'c2').status, 'done', 'relase next live element')
  W.jtac.alive = false
  W.advance(1)
  eq(#W.live(), 0, 'spot destroyed with controller')
  s = bridge(W, 'e1', reply('e1', { command('c3', 'laser_on') }))
  eq(ackFor(s, 'c3').status, 'failed')
  eq(ackFor(s, 'c3').reason, 'controller not available')
end)

check('line of sight is required and its loss stops the laser', function()
  local W = standardMission()
  W.visible = false
  local s = bridge(W, 'e1', reply('e1', { command('c1', 'laser_on') }))
  eq(ackFor(s, 'c1').status, 'failed')
  eq(ackFor(s, 'c1').reason, 'no line of sight')
  W.visible = true
  bridge(W, 'e1', reply('e1', { command('c2', 'laser_on') }))
  W.visible = false
  W.advance(1)
  eq(#W.live(), 0)
end)

check('cancel before execution blocks the command permanently; expiry and authorisation enforced', function()
  local W = standardMission()
  local s = bridge(W, 'e1', reply('e1', {}, { 'c1' }))
  eq(ackFor(s, 'c1').status, 'cancelled')
  s = bridge(W, 'e1', reply('e1', { command('c1', 'laser_on') }))
  eq(ackFor(s, 'c1').status, 'cancelled', 'late command after cancel')
  eq(#W.spots, 0)
  W.now = 50
  s = bridge(W, 'e1', reply('e1', { command('c2', 'laser_on', { deadline = 49 }) }))
  eq(ackFor(s, 'c2').status, 'expired')
  s = bridge(W, 'e1', reply('e1', { command('c3', 'mark', { kind = 'illumination' }),
                                    command('c4', 'mark', { kind = 'laser' }),
                                    command('c5', 'laser_on', { task = 7 }),
                                    command('c6', 'mark', { kind = 'smoke' }) }))
  eq(ackFor(s, 'c3').status, 'rejected', 'unauthorised mark kind')
  eq(ackFor(s, 'c4').status, 'rejected', 'laser only through laser_on')
  eq(ackFor(s, 'c5').status, 'rejected', 'unknown task')
  eq(ackFor(s, 'c6').status, 'done')
  eq(#W.smoke, 1)
  eq(W.smoke[1].color, 2, 'white smoke')
  eq(#W.illumination, 0)
  eq(#W.spots, 0)
end)

check('laser off, lease expiry, epoch change and mission end destroy spots', function()
  local W = standardMission()
  bridge(W, 'e1', reply('e1', { command('c1', 'laser_on') }))
  local s = bridge(W, 'e1', reply('e1', { command('c2', 'laser_off') }))
  eq(ackFor(s, 'c2').status, 'done')
  eq(#W.live(), 0, 'laser off')
  bridge(W, 'e1', reply('e1', { command('c3', 'laser_on') }))
  W.advance(10)
  eq(#W.live(), 1, 'lease still valid')
  W.advance(6)
  eq(#W.live(), 0, 'lease expired without hook calls')
  bridge(W, 'e1', reply('e1', { command('c4', 'laser_on') }))
  s = bridge(W, 'e2', reply('e1', { command('c5', 'laser_on') }))
  eq(#W.live(), 0, 'epoch change destroyed spot')
  eq(ackFor(s, 'c5'), nil, 'previous epoch response ignored')
  eq(s.seq, 1, 'sequence restarted')
  bridge(W, 'e2', reply('e2', { command('c6', 'laser_on') }))
  W.handlers[1]:onEvent({ id = 12 })
  eq(#W.live(), 0, 'mission end')
  eq(W.env.OverlordJTAC.bridge('e2', ''), nil, 'stopped bridge')
end)

check('malformed brain responses are ignored without executing anything', function()
  local W = standardMission()
  for _, raw in ipairs({ 'not json', '{"version":2,"accepted":true,"epoch":"e1","commands":[]}',
                         '{"version":1,"accepted":false,"epoch":"e1","commands":[{"id":"x","op":"laser_on","task":1,"deadline":9}]}',
                         string.rep('x', 70000) }) do
    local s = decode(W.env.OverlordJTAC.bridge('e1', raw))
    eq(#s.acks == 0 or next(s.acks) == nil, true, 'no acks')
  end
  eq(#W.spots, 0)
  eq(W.env.OverlordJTAC.bridge('bad epoch!', ''), nil, 'epoch characters validated')
end)

check('only fresh accepted brain responses renew the laser authorization lease', function()
  local W = standardMission()
  local s = bridge(W, 'e1')
  local accepted = reply('e1', { command('lease-laser', 'laser_on') }, {}, s.seq)
  bridge(W, 'e1', accepted)
  W.advance(10)
  bridge(W, 'e1', reply('e1'))
  W.advance(10)
  eq(#W.live(), 1, 'fresh brain response renews lease')
  for _ = 1, 16 do
    W.advance(1)
    bridge(W, 'e1', accepted) -- old successful body is not new authorization
    bridge(W, 'e1')           -- reachable hook without reachable brain
  end
  eq(#W.live(), 0, 'brain outage expires spot despite hook polls and replay')
end)

check('old response cannot resurrect a cancellation after bounded id cache eviction', function()
  local W = standardMission()
  local initial = bridge(W, 'e1')
  local old = reply('e1', { command('cancelled-laser', 'laser_on') }, {}, initial.seq)
  bridge(W, 'e1', reply('e1', {}, { 'cancelled-laser' }))
  for i = 1, 140 do
    bridge(W, 'e1', reply('e1', { command('new-' .. i, 'mark', { kind = 'smoke' }) }))
  end
  bridge(W, 'e1', old)
  eq(#W.live(), 0, 'retired response sequence must not execute old laser')
  eq(#W.smoke, 140, 'new commands exercised id-cache eviction')
end)

check('controller loss before command execution refuses positive marking', function()
  local W = standardMission()
  bridge(W, 'e1')
  W.jtac.alive = false
  local snapshot = bridge(W, 'e1', reply('e1', { command('stale-smoke', 'mark', { kind = 'smoke' }) }))
  eq(ackFor(snapshot, 'stale-smoke').status, 'failed')
  eq(#W.smoke, 0, 'dead controller must not mark')
end)

check('target lookup loss is not affirmative destruction evidence', function()
  local missing = standardMission()
  bridge(missing, 'e1')
  missing.groups['Red Armor Column'] = nil
  local lost = bridge(missing, 'e1')
  eq(lost.tasks[1].target.alive, false)
  eq(lost.tasks[1].target.destroyed, false, 'missing lookup is unconfirmed')
  local killed = standardMission()
  bridge(killed, 'e2')
  killed.kill(killed.t1)
  killed.kill(killed.t2)
  killed.kill(killed.t3)
  local dead = bridge(killed, 'e2')
  eq(dead.tasks[1].target.destroyed, true, 'observed deaths substantiate BDA')
end)

-- Server hook ---------------------------------------------------------------------

local function newHook(W, opts)
  opts = opts or {}
  local H = { real = 0, requests = {}, calls = {}, logs = {}, mode = opts.mode or 'up', replies = {},
              callbacks = nil, conns = {} }
  local dir = os.tmpname()
  os.remove(dir)
  local cfgDir = dir .. '/Config/'
  os.execute('mkdir -p "' .. cfgDir .. '"')
  local f = assert(io.open(cfgDir .. 'overlord-hook.lua', 'w'))
  f:write(opts.config or 'return { host = "127.0.0.1", port = 8081, bridgeToken = "bridge-secret",'
    .. 'apiToken = "api-secret", interval = 1, timeout = 3, state = "server" }')
  f:close()
  H.dir = dir

  local function respond(conn)
    local split = conn.data:find('\r\n\r\n', 1, true)
    local headers, body = conn.data:sub(1, split - 1), conn.data:sub(split + 4)
    local request = { headers = headers, body = decode(body) }
    H.requests[#H.requests + 1] = request
    if H.mode == 'stall' then return end
    local status, payload = 200, table.remove(H.replies, 1)
    if H.mode == 'error' then status, payload = 401, '{"error":"unauthorized"}' end
    if type(payload) == 'function' then payload = payload(request) end
    payload = payload or encode(reply(request.body.epoch, {}, {}, request.body.seq))
    conn.response = 'HTTP/1.0 ' .. status .. ' OK\r\nContent-Type: application/json\r\nContent-Length: '
      .. #payload .. '\r\n\r\n' .. payload
    conn.offset = 0
  end

  local socket = {}
  function socket.tcp()
    local conn = { data = '', closed = false, partial = false }
    H.conns[#H.conns + 1] = conn
    function conn:settimeout(t) self.timeout = t end
    function conn:connect()
      if H.mode == 'offline' then return nil, 'connection refused' end
      return nil, 'timeout'
    end
    function conn:send(data, i)
      if not self.partial then   -- first call delivers a fragment, as a full socket buffer would
        self.partial = true
        self.data = self.data .. data:sub(i, i + 19)
        return nil, 'timeout', i + 19
      end
      self.data = self.data .. data:sub(i)
      respond(self)
      return #data
    end
    function conn:receive(n)
      if not self.response then return nil, 'timeout', '' end
      if self.offset >= #self.response then return nil, 'closed', '' end
      local chunk = self.response:sub(self.offset + 1, self.offset + math.min(n, 40))
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
  H.env = env
  function H.frames(n, step)
    for _ = 1, n do
      H.real = H.real + (step or 0.02)
      H.callbacks.onSimulationFrame()
    end
  end
  function H.exchange()
    local before = #H.requests
    H.real = H.real + 1.1
    H.callbacks.onSimulationFrame()
    for _ = 1, 50 do
      if H.conns[#H.conns] and H.conns[#H.conns].closed then break end
      H.frames(1)
    end
    assert(#H.requests == before + 1, 'one request per exchange')
    return H.requests[#H.requests]
  end
  function H.cleanup() os.execute('rm -rf "' .. dir .. '"') end
  return H
end

check('hook relays snapshots with both auth headers through the fixed entry point', function()
  local W = standardMission()
  local H = newHook(W)
  H.callbacks.onSimulationStart()
  local request = H.exchange()
  assert(request.headers:find('^POST /dcs/exchange HTTP/1.1\r\n'), 'request line')
  assert(request.headers:find('\r\nX%-DCS%-Token: bridge%-secret'), 'bridge token header')
  assert(request.headers:find('\r\nAuthorization: Bearer api%-secret'), 'api token header')
  eq(request.body.version, 1)
  eq(request.body.seq, 1)
  assert(request.body.epoch:match('^%d+%-%d+$'), 'hook epoch')
  eq(H.calls[1].state, 'server')
  assert(H.calls[1].code:match('^return OverlordJTAC and OverlordJTAC%.bridge%(".-",""%)$'), 'fixed call')
  for _, line in ipairs(H.logs) do assert(not line:find('secret'), 'token logged') end
  H.cleanup()
end)

check('hook delivers commands next exchange and returns the DCS acknowledgement', function()
  local W = standardMission()
  local H = newHook(W)
  H.callbacks.onSimulationStart()
  local first = H.exchange()
  H.replies[1] = function(request)
    return encode(reply(request.body.epoch, { command('c1', 'laser_on') }, {}, request.body.seq))
  end
  H.exchange()
  local third = H.exchange()
  eq(ackFor(third.body, 'c1').status, 'done')
  eq(third.body.tasks[1].laser.on, true)
  eq(#W.live(), 1)
  H.callbacks.onSimulationStop()
  H.callbacks.onSimulationStart()
  local fresh = H.exchange()
  eq(fresh.body.seq, 1, 'new epoch after restart')
  eq(#W.live(), 0, 'restart destroyed spot')
  H.cleanup()
end)

check('hostile response bytes stay a string literal', function()
  local W = standardMission()
  local H = newHook(W)
  H.callbacks.onSimulationStart()
  local first = H.exchange()
  H.replies[1] = '"]] ) os.exit(1) --\n\r\0' .. "'" .. ' {"version":1}'
  H.exchange()
  H.exchange()
  eq(#W.forbidden, 0, 'no code from the response ran')
  local code = H.calls[3].code
  assert(code:find('os.exit', 1, true), 'payload carried as data')
  assert(code:match('^return OverlordJTAC and OverlordJTAC%.bridge%('), 'fixed prefix')
  eq(#W.spots, 0)
  H.cleanup()
  eq(first.body.seq, 1)
end)

check('offline, stalled and rejecting brains never block or leak commands', function()
  local W = standardMission()
  local H = newHook(W, { mode = 'offline' })
  H.callbacks.onSimulationStart()
  H.real = H.real + 1.1
  H.callbacks.onSimulationFrame()
  assert(H.conns[#H.conns].closed, 'refused connect closes the socket')
  H.mode = 'stall'
  H.real = H.real + 1.1
  H.callbacks.onSimulationFrame()
  H.frames(5)
  assert(not H.conns[#H.conns].closed, 'stalled socket still pending')
  H.frames(1, 3.5)
  assert(H.conns[#H.conns].closed, 'socket closed')
  H.mode = 'error'
  H.exchange()
  H.exchange()
  assert(not H.calls[#H.calls].code:find('unauthorized', 1, true),
    'non-200 body never reaches the mission')
  H.cleanup()
end)

check('hook stays disabled without a bridge token', function()
  local W = standardMission()
  local H = newHook(W, { config = 'return { host = "127.0.0.1", port = 8080 }' })
  H.callbacks.onSimulationStart()
  H.frames(100, 0.1)
  eq(#H.calls, 0, 'no mission calls')
  eq(#H.requests, 0, 'no requests')
  H.cleanup()
end)

check('hook rejects DNS and public destinations before creating sockets', function()
  for _, host in ipairs({ 'localhost', '8.8.8.8', '169.254.169.254',
                           '127.0.0.1.evil', '010.0.0.1', '256.1.1.1' }) do
    local H = newHook(standardMission(), { config = 'return { host = '
      .. string.format('%q', host) .. ', bridgeToken = "server-secret" }' })
    H.callbacks.onSimulationStart()
    H.frames(20, 0.1)
    eq(#H.conns, 0, 'unsafe host must not open socket')
    eq(#H.calls, 0, 'unsafe config stays disabled')
    H.cleanup()
  end
end)

check('default hook features stay JTAC-only on the original route', function()
  local W = standardMission()
  local H = newHook(W)
  H.callbacks.onSimulationStart()
  for _ = 1, 3 do
    local request = H.exchange()
    assert(request.headers:find('^POST /dcs/exchange HTTP/1.1\r\n'), 'jtac route')
  end
  for _, call in ipairs(H.calls) do
    assert(not call.code:find('OverlordCarrier', 1, true), 'carrier entry point called without the feature')
    assert(not call.code:find('OverlordAirfield', 1, true), 'airfield entry point called without the feature')
  end
  H.cleanup()
end)

check('enabling carriers keeps JTAC commands working when no carrier script is loaded', function()
  local W = standardMission()
  local H = newHook(W, { config = 'return { host = "127.0.0.1", port = 8081, bridgeToken = "bridge-secret",'
    .. ' interval = 1, timeout = 3, state = "server", features = { "jtac", "carriers" } }' })
  H.callbacks.onSimulationStart()
  H.exchange()
  H.replies[1] = function(request)
    return encode(reply(request.body.epoch, { command('c1', 'laser_on') }, {}, request.body.seq))
  end
  H.exchange()
  local third = H.exchange()
  assert(third.headers:find('^POST /dcs/exchange HTTP/1.1\r\n'), 'jtac route')
  eq(ackFor(third.body, 'c1').status, 'done')
  eq(#W.live(), 1)
  local jtac, carrier = 0, 0
  for _, call in ipairs(H.calls) do
    if call.code:match('^return OverlordJTAC and OverlordJTAC%.bridge%(') then jtac = jtac + 1 end
    if call.code:match('^return OverlordCarrier and OverlordCarrier%.bridge%(".-",""%)$') then carrier = carrier + 1 end
  end
  eq(jtac, 3, 'jtac calls')
  eq(carrier, 3, 'carrier entry point polled with an empty response')
  for _, request in ipairs(H.requests) do
    assert(not request.headers:find('/carrier/exchange', 1, true), 'missing carrier script posts nothing')
  end
  H.cleanup()
end)

check('hook rejects unknown, duplicate or malformed feature lists', function()
  for _, features in ipairs({ '"jtac"', '{}', '{ "jtac", "jtac" }', '{ "awacs" }', '{ jtac = true }',
                              '{ "jtac", path = "/admin" }', '{ "OverlordJTAC" }', '{ "airfields", "airfields" }',
                              '{ "airfield" }' }) do
    local H = newHook(standardMission(), { config = 'return { host = "127.0.0.1", bridgeToken = "server-secret",'
      .. ' features = ' .. features .. ' }' })
    H.callbacks.onSimulationStart()
    H.frames(20, 0.1)
    eq(#H.calls, 0, 'invalid features ' .. features .. ' must disable the hook')
    eq(#H.conns, 0, 'no sockets for ' .. features)
    H.cleanup()
  end
end)

print(string.format('%d passed, %d failed (offline doubles; not a real DCS acceptance test)', passed, failures))
if failures > 0 then os.exit(1) end
