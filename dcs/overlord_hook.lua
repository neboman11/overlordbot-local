--[[
Overlord bridge hook (DCS server GUI/hook environment).

Install: copy to  Saved Games/DCS(.server)/Scripts/Hooks/overlord_hook.lua  on the
DCS server only.  Configuration lives in the server-only file
Saved Games/DCS(.server)/Config/overlord-hook.lua, which is never part of a
mission and therefore never downloaded by clients:

  return {
    host = "127.0.0.1", -- canonical numeric loopback/RFC1918 IPv4 only; no DNS
    port = 8081,        -- brain HTTP port
    bridgeToken = "<DCS_BRIDGE_TOKEN>",  -- required, sent as X-DCS-Token
    apiToken = "<API_TOKEN>",    -- only when the brain has API_TOKEN set (Authorization: Bearer)
    interval = 1,                -- seconds between exchanges (0.5-10)
    timeout = 3,                 -- absolute seconds per exchange (1-10)
    state = "server",            -- optional; the only accepted value (see below)
    features = { "jtac" },       -- optional; any of "jtac", "carriers", "airfields" (default { "jtac" })
    autoLoad = false,            -- optional; true loads the fixed static companions (see below)
    airfieldRadioFiles = {       -- optional, airfields only: theatre -> installed native Radio.lua
      Caucasus = "C:\\Program Files\\Eagle Dynamics\\DCS World\\Mods\\terrains\\Caucasus\\Radio.lua",
    },
  }

Each feature is a fixed pair of mission entry point and brain route:

  jtac       OverlordJTAC.bridge(<epoch>, <last brain response>)     -> POST /dcs/exchange
  carriers   OverlordCarrier.bridge(<epoch>, <last brain response>)  -> POST /carrier/exchange
  airfields  OverlordAirfield.bridge(<epoch>, <last brain response>, <native radio context>)
                                                                     -> POST /airfield/exchange

Every interval the hook calls each enabled feature's entry point through
net.dostring_in("server", ...) - the Lua state that runs mission scripts (the
"mission" state only holds the mission description; world is nil there and the
hook refuses state = "mission") - with every argument passed as a %q-escaped string literal, so no
brain or mission data is ever executed as code, then POSTs the returned
snapshot to that feature's route.  Features share the configuration, tokens,
epoch and transport code, but each has its own pending response, in-flight
request, schedule and error state: a feature whose mission script is missing,
fails or whose exchange stalls never delays the other.  Networking is a
non-blocking LuaSocket state machine: each simulation frame does at most a few
zero-timeout socket calls per feature, an offline brain costs nothing but a
failed connect, and every exchange has an absolute deadline.  Tokens are never
logged.  Simulation stop/start resets every feature and starts a new epoch.

Auto-load (optional, autoLoad = true, default false): missions need no DO SCRIPT
FILE triggers.  When an enabled feature's mission module is still undefined one
interval after simulation start (so MISSION START triggers run first), the hook
reads that feature's fixed companion file, and only it:

  jtac       <writedir>/Scripts/Overlord/overlord_jtac.lua
  carriers   <writedir>/Scripts/Overlord/overlord_carrier.lua
  airfields  <writedir>/Scripts/Overlord/overlord_airfield.lua

Names come from the fixed feature table; configuration, the brain and the mission
never supply a path or code.  Each file is read at most once per epoch, must be
non-empty source text (precompiled chunks refused) of at most 1 MiB, and is
handed to the sanitized mission as one %q string literal; the mission compiles it
with loadstring only if the module is still undefined, in the same net.dostring_in
call, so a module the mission configured itself is never replaced.  Mission
config globals (OverlordJTACConfig, ...) keep working; without them each companion
uses its defaults.  The .miz is never modified.  A missing or failing companion
only disables its own feature until the next epoch.

Airfield radios: published airfield frequencies exist only in the installed
terrain's native Radio.lua, which the sanitized mission cannot read.  Once per
epoch the airfields feature reads it HERE, in a private sandbox: the file named
for the mission theatre in airfieldRadioFiles (absolute local path ending in
.lua; no "..", no UNC/network path), otherwise <DCS install>/Mods/terrains/
<theatre>/Radio.lua.  The file is size-limited, precompiled chunks are refused,
and it runs as data only: an empty environment with just the translation helper
_(), the band constants HF/VHF_LOW/VHF_HI/UHF, MODULATIONTYPE_AM/FM, a
require that only provides the i_18n translation stub and a dofile stub that
accepts only the native front matter's Scripts/World/Radio/ModulationTypes.lua
and FrequencyBands.lua (constants already provided; nothing is read or run);
no io, os, lfs, load or string methods, and an instruction limit (the file is not run at all when
the debug hook API is unavailable).  Only radioId "airfield<N>_<k>"
records with ground/tower/approach roles become the normalized context
  { theatre, radios = { { airfield_id, frequency_hz, modulation, roles, aliases } }, error? }
handed to the mission as a string literal.  No path, code or secret is passed;
nothing from the brain or the mission ever selects a file.  The mission rejects
a context for another theatre.  MissionScripting.lua sanitization stays
untouched.

Version sensitivity: net.dostring_in from hooks is the only bridge into the
sanitized mission environment.  Eagle Dynamics changed its permissions in July
2025 (then reverted, with a future GUI toggle announced).  If your DCS build
gates net.dostring_in, enable it for this hook through the official setting;
do not desanitize MissionScripting.lua - the mission scripts need no io, lfs,
os or socket access.
]]

package.path = package.path .. ';.\\LuaSocket\\?.lua'
package.cpath = package.cpath .. ';.\\LuaSocket\\?.dll'

local socket = require('socket')

local MAX_SNAPSHOT, MAX_RESPONSE = 262144, 65536
local MAX_RADIO_FILE, MAX_RADIO_RECORDS, MAX_RADIO_ALIASES, RADIO_INSTRUCTIONS = 4194304, 4096, 8, 20000000
local MAX_COMPANION_FILE = 1048576
-- net.dostring_in Lua state holding the mission scripting API (world, coalition, ...).  The state
-- named "mission" is the mission description (world is nil there), so it is refused.
local SCRIPTING_STATE = 'server'
-- Fixed feature table: configuration selects entries, it never supplies code or paths.
local FEATURES = {
  jtac = { module = 'OverlordJTAC', path = '/dcs/exchange', file = 'overlord_jtac.lua' },
  carriers = { module = 'OverlordCarrier', path = '/carrier/exchange', file = 'overlord_carrier.lua' },
  airfields = { module = 'OverlordAirfield', path = '/airfield/exchange', file = 'overlord_airfield.lua',
                radioContext = true },
}
local RADIO_ROLES = { 'ground', 'tower', 'approach' }
local hook = { cfg = nil, epoch = nil, features = {}, radioContext = nil }

local function logError(feature, message)
  if message ~= feature.lastError then
    feature.lastError = message
    log.write('OVERLORD', log.ERROR, 'Overlord bridge [' .. feature.name .. ']: ' .. message)
  end
end

local function headerSafe(value)
  return type(value) == 'string' and #value >= 1 and #value <= 512 and not value:find('[%c ]')
end

local function privateIPv4(host)
  if type(host) ~= 'string' then return false end
  local parts = { host:match('^(%d+)%.(%d+)%.(%d+)%.(%d+)$') }
  if #parts ~= 4 then return false end
  for i, text in ipairs(parts) do
    local value = tonumber(text)
    if not value or value > 255 or tostring(value) ~= text then return false end
    parts[i] = value
  end
  return parts[1] == 127 or parts[1] == 10
    or (parts[1] == 172 and parts[2] >= 16 and parts[2] <= 31)
    or (parts[1] == 192 and parts[2] == 168)
end

local function featureList(raw)
  if raw == nil then return { 'jtac' } end
  if type(raw) ~= 'table' then return nil end
  local names, seen, count = {}, {}, 0
  for _ in pairs(raw) do count = count + 1 end
  if count == 0 or count ~= #raw then return nil end
  for _, name in ipairs(raw) do
    if type(name) ~= 'string' or not FEATURES[name] or seen[name] then return nil end
    seen[name] = true
    names[#names + 1] = name
  end
  return names
end

local function theatreName(value)
  return type(value) == 'string' and #value >= 1 and #value <= 64 and value:find('^[%w_%-]+$') ~= nil
end

-- Absolute local path of a .lua file: no traversal, no UNC/network share, no control bytes.
local function radioPath(value)
  if type(value) ~= 'string' or #value < 5 or #value > 512 or value:find('%c') then return false end
  if value:find('^[\\/][\\/]') or not (value:find('^%a:[\\/]') or value:find('^/')) then return false end
  if not value:lower():find('%.lua$') then return false end
  for part in value:gmatch('[^\\/]+') do
    if part == '..' or part == '.' then return false end
  end
  return true
end

local function radioFiles(raw)
  if raw == nil then return {} end
  if type(raw) ~= 'table' then return nil end
  local files = {}
  for theatre, path in pairs(raw) do
    if not theatreName(theatre) or not radioPath(path) then return nil end
    files[theatre] = path
  end
  return files
end

local function loadConfig(path)
  local env = {}
  local chunk = setfenv and loadfile(path) or loadfile(path, 't', env)
  if not chunk then return nil, 'missing or unreadable ' .. path end
  if setfenv then setfenv(chunk, env) end
  local ok, cfg = pcall(chunk)
  if not ok or type(cfg) ~= 'table' then return nil, 'configuration must return a table' end
  local host, port = cfg.host or '127.0.0.1', cfg.port or 8081
  if not privateIPv4(host) or type(port) ~= 'number'
      or port < 1 or port > 65535 or port ~= math.floor(port) then
    return nil, 'host must be numeric loopback/RFC1918 IPv4; port must be valid'
  end
  if not headerSafe(cfg.bridgeToken) or (cfg.apiToken ~= nil and not headerSafe(cfg.apiToken)) then
    return nil, 'bridgeToken required; tokens must be printable without spaces'
  end
  local interval, timeout = cfg.interval or 1, cfg.timeout or 3
  if type(interval) ~= 'number' or interval < 0.5 or interval > 10
      or type(timeout) ~= 'number' or timeout < 1 or timeout > 10 then
    return nil, 'invalid interval or timeout'
  end
  if cfg.state ~= nil and cfg.state ~= SCRIPTING_STATE then
    return nil, 'state must be "server" (the "mission" state has no mission scripting API)'
  end
  local state = SCRIPTING_STATE
  local features = featureList(cfg.features)
  if not features then return nil, 'features must be a list of distinct "jtac"/"carriers"/"airfields" entries' end
  local files = radioFiles(cfg.airfieldRadioFiles)
  if not files then
    return nil, 'airfieldRadioFiles must map theatre names to absolute local .lua paths without ".."'
  end
  if cfg.autoLoad ~= nil and type(cfg.autoLoad) ~= 'boolean' then return nil, 'autoLoad must be true or false' end
  return { host = host, port = port, bridgeToken = cfg.bridgeToken, apiToken = cfg.apiToken,
           interval = interval, timeout = timeout, state = state, features = features, radioFiles = files,
           autoLoad = cfg.autoLoad == true }
end

-- Native terrain radio data (airfields feature) ------------------------------------------

local function translate(value) return value end
local UNKNOWN = {}   -- value of any undefined name inside Radio.lua: never a number, never callable

-- Native Radio.lua front matter dofile()s exactly these constant tables, already seeded below.
local RADIO_CONSTANT_FILES = {
  ['Scripts/World/Radio/ModulationTypes.lua'] = true,
  ['Scripts/World/Radio/FrequencyBands.lua'] = true,
}

-- Runs the installed Radio.lua as data only and returns its `radio` table, or nil and a reason.
local function runRadioData(source)
  if source:byte(1) == 27 then return nil, 'precompiled chunk refused' end
  local env = setmetatable({
    _ = translate, HF = 0, VHF_LOW = 1, VHF_HI = 2, UHF = 3, MODULATIONTYPE_AM = 0, MODULATIONTYPE_FM = 1,
    require = function(name)
      if name == 'i_18n' then return { translate = translate } end
      error('module unavailable', 0)
    end,
    -- Stub: no file is read or run; any other argument is an error.
    dofile = function(path)
      if not RADIO_CONSTANT_FILES[path] then error('file unavailable', 0) end
    end,
  }, { __index = function() return UNKNOWN end })
  local chunk
  if setfenv then
    chunk = loadstring(source, '=Radio.lua')
    if chunk then setfenv(chunk, env) end
  else
    chunk = load(source, '=Radio.lua', 't', env)
  end
  if not chunk then return nil, 'syntax error' end
  if type(jit) == 'table' and type(jit.off) == 'function' then jit.off(chunk, true) end   -- keep the hook effective
  -- The data file only runs under an instruction limit; without one it is refused, never run unbounded.
  if type(debug) ~= 'table' or type(debug.sethook) ~= 'function' or type(debug.gethook) ~= 'function' then
    return nil, 'instruction limit unavailable'
  end
  local sethook, gethook = debug.sethook, debug.gethook
  local okOld, oldHook, oldMask, oldCount = pcall(gethook)
  if not okOld then return nil, 'instruction limit unavailable' end
  local strings = getmetatable('')
  local methods = type(strings) == 'table' and strings.__index or nil
  -- Idempotent: restores the previous hook and the string methods.  The limit callback runs it
  -- BEFORE raising, so whatever instruction the count lands on (inside the chunk, at its return,
  -- or during cleanup itself) the hook state and string methods are already restored when the
  -- error propagates; outside pcall(chunk) that error reaches the caller's pcall.
  local function cleanup()
    if oldHook then pcall(sethook, oldHook, oldMask, oldCount) else pcall(sethook) end
    if methods then strings.__index = methods end
  end
  local okLimit = pcall(sethook, function()
    cleanup()
    error('instruction limit exceeded', 0)
  end, '', RADIO_INSTRUCTIONS)
  if not okLimit then
    cleanup()
    return nil, 'instruction limit unavailable'
  end
  if methods then strings.__index = {} end   -- no string methods (rep/format/dump) inside the data file
  local ok = pcall(chunk)
  cleanup()
  if not ok then return nil, 'execution refused' end
  local radio = rawget(env, 'radio')
  if type(radio) ~= 'table' then return nil, 'no radio table' end
  return radio
end

-- Callsign aliases: printable ASCII common/nato names, deduplicated, bounded.
local function radioAliases(callsign)
  local out, seen = {}, {}
  for _, entry in ipairs(type(callsign) == 'table' and callsign or {}) do
    for _, key in ipairs({ 'common', 'nato' }) do
      local names = type(entry) == 'table' and entry[key]
      for _, name in ipairs(type(names) == 'table' and names or {}) do
        if type(name) == 'string' and #name >= 1 and #name <= 64 and not name:find('[^\32-\126]')
            and name:find('%S') and not seen[name] and #out < MAX_RADIO_ALIASES then
          seen[name] = true
          out[#out + 1] = name
        end
      end
    end
  end
  return out
end

-- Only radioId "airfield<N>_<k>" records with ground/tower/approach roles; each valid
-- {modulation, Hz} band entry becomes one radio.  Returns radios and the count of control
-- records that publish no usable frequency, or nil and a reason.
local function normalizeRadios(radio)
  local radios, records, unusable = {}, 0, 0
  for _, record in pairs(radio) do
    records = records + 1
    if records > MAX_RADIO_RECORDS then return nil, 'too many radio records' end
    local digits = type(record) == 'table' and type(record.radioId) == 'string'
      and record.radioId:match('^airfield(%d+)_%d+$')
    local id = tonumber(digits)
    if id and id >= 1 and id <= 2147483647 then
      local roleSet, roles = {}, {}
      for _, role in ipairs(type(record.role) == 'table' and record.role or {}) do roleSet[role] = true end
      for _, role in ipairs(RADIO_ROLES) do if roleSet[role] then roles[#roles + 1] = role end end
      if #roles > 0 then
        local frequencies, found = type(record.frequency) == 'table' and record.frequency or {}, false
        for _, value in pairs(frequencies) do
          local modulation, hz = type(value) == 'table' and value[1], type(value) == 'table' and value[2]
          if (modulation == 0 or modulation == 1) and type(hz) == 'number' and hz >= 1000000 and hz < 1000000000
              and hz == math.floor(hz) then
            if #radios >= MAX_RADIO_RECORDS then return nil, 'too many radio frequencies' end
            radios[#radios + 1] = { airfield_id = string.format('%d', id), frequency_hz = hz, modulation = modulation,
                                    roles = roles, aliases = radioAliases(record.callsign) }
            found = true
          end
        end
        if not found then unusable = unusable + 1 end
      end
    end
  end
  table.sort(radios, function(a, b)
    if a.airfield_id ~= b.airfield_id then return tonumber(a.airfield_id) < tonumber(b.airfield_id) end
    if a.frequency_hz ~= b.frequency_hz then return a.frequency_hz < b.frequency_hz end
    return a.modulation < b.modulation
  end)
  return radios, unusable
end

-- Whole local file, non-empty and at most `limit` bytes, or nil and a reason naming `label`.
-- The hook's io file objects have read/close but no seek: read at most limit + 1 bytes.
local function readBounded(path, limit, label)
  local file = io.open(path, 'rb')
  if not file then return nil, label .. ' not readable' end
  local ok, source = pcall(function() return file:read(limit + 1) end)
  pcall(function() file:close() end)
  if not ok or type(source) ~= 'string' or source == '' then return nil, label .. ' read failed' end
  if #source > limit then return nil, label .. ' too large' end
  return source
end

local function radioContext()
  local ok, current = pcall(DCS.getCurrentMission)
  local theatre = ok and type(current) == 'table' and type(current.mission) == 'table' and current.mission.theatre
  if not theatreName(theatre) then return { radios = {}, error = 'mission theatre unavailable' } end
  local path = hook.cfg.radioFiles[theatre]
  if not path then
    local okDir, dir = pcall(lfs.currentdir)
    if not okDir or type(dir) ~= 'string' or dir == '' then
      return { theatre = theatre, radios = {}, error = 'DCS install directory unknown; set airfieldRadioFiles' }
    end
    if not dir:find('[\\/]$') then dir = dir .. '/' end
    path = dir .. 'Mods/terrains/' .. theatre .. '/Radio.lua'
  end
  local source, err = readBounded(path, MAX_RADIO_FILE, 'native Radio.lua')
  local radio, radios, unusable
  if source then radio, err = runRadioData(source) end
  if radio then
    radios, unusable = normalizeRadios(radio)
    if not radios then err = unusable end
  end
  if not radios then
    log.write('OVERLORD', log.ERROR, 'Overlord bridge [airfields]: ' .. theatre .. ' radio data rejected: '
      .. err .. ' (' .. path .. ')')
    return { theatre = theatre, radios = {}, error = err }
  end
  log.write('OVERLORD', log.INFO, 'Overlord bridge [airfields]: ' .. #radios .. ' native airfield radios for '
    .. theatre)
  local out = { theatre = theatre, radios = radios }
  if unusable > 0 then out.error = unusable .. ' native airfield radio records publish no usable frequency' end
  return out
end

-- Normalized context JSON, built once per epoch; failures become an explicit error context.
local function encodedRadioContext()
  local ok, context = pcall(radioContext)
  if not ok or type(context) ~= 'table' then context = { radios = {}, error = 'native radio data failed' } end
  local okJson, encoded = pcall(net.lua2json, context)
  if not okJson or type(encoded) ~= 'string' or #encoded > MAX_SNAPSHOT then
    encoded = net.lua2json({ theatre = context.theatre, radios = {}, error = 'native radio context too large' })
  end
  return encoded
end

local function close(feature, failure)
  local request = feature.inflight
  feature.inflight = nil
  if request then pcall(function() request.tcp:close() end) end
  if failure then logError(feature, failure) end
end

local function finish(feature, request)
  local response = table.concat(request.chunks)
  local status = tonumber(response:match('^HTTP/1%.%d (%d%d%d)'))
  local split = response:find('\r\n\r\n', 1, true)
  if status ~= 200 or not split then
    close(feature, 'brain answered ' .. tostring(status))
    return
  end
  local headers, body = response:sub(1, split - 1), response:sub(split + 4)
  local length = tonumber(headers:lower():match('\ncontent%-length:%s*(%d+)'))
  if length and #body < length then
    close(feature, 'truncated response')
    return
  end
  body = length and body:sub(1, length) or body
  close(feature, nil)
  feature.inbound = body
  feature.lastError = nil
end

local function start(feature, body)
  local cfg = hook.cfg
  local lines = {
    'POST ' .. feature.path .. ' HTTP/1.1',
    'Host: ' .. cfg.host .. ':' .. cfg.port,
    'Content-Type: application/json',
    'Content-Length: ' .. #body,
    'Connection: close',
    'X-DCS-Token: ' .. cfg.bridgeToken,
  }
  if cfg.apiToken then lines[#lines + 1] = 'Authorization: Bearer ' .. cfg.apiToken end
  local tcp = socket.tcp()
  tcp:settimeout(0)
  local ok, err = tcp:connect(cfg.host, cfg.port)
  if not ok and err ~= 'timeout' and err ~= 'Operation already in progress' then
    pcall(function() tcp:close() end)
    logError(feature, 'connect failed: ' .. tostring(err))
    return
  end
  feature.inflight = { tcp = tcp, out = table.concat(lines, '\r\n') .. '\r\n\r\n' .. body, sent = 0,
                       chunks = {}, size = 0, connected = ok == 1,
                       deadline = DCS.getRealTime() + cfg.timeout }
end

local function pump(feature, now)
  local request = feature.inflight
  if now > request.deadline then
    close(feature, 'exchange timed out')
    return
  end
  if not request.connected then
    local _, writable = socket.select(nil, { request.tcp }, 0)
    if not writable or #writable == 0 then return end
    request.connected = true
  end
  while request.sent < #request.out do
    local last, err, partial = request.tcp:send(request.out, request.sent + 1)
    if last then
      request.sent = last
    elseif err == 'timeout' then
      request.sent = partial or request.sent
      return
    else
      close(feature, 'send failed: ' .. tostring(err))
      return
    end
  end
  for _ = 1, 16 do
    local data, err, partial = request.tcp:receive(8192)
    local chunk = data or partial
    if chunk and #chunk > 0 then
      request.size = request.size + #chunk
      if request.size > MAX_RESPONSE + 4096 then
        close(feature, 'response too large')
        return
      end
      request.chunks[#request.chunks + 1] = chunk
    end
    if err == 'closed' then
      finish(feature, request)
      return
    elseif err then
      if err ~= 'timeout' then close(feature, 'receive failed: ' .. tostring(err)) end
      return
    end
  end
end

-- Auto-load ---------------------------------------------------------------------------

local AUTOLOAD_RESULTS = {
  unavailable = 'mission has no loadstring',
  syntax = 'companion does not compile',
  runtime = 'companion raised an error',
  missing = 'companion did not define its module',
}

-- Loads the feature's fixed companion into the mission once per epoch, never over a module
-- the mission already defines.  The source travels only as a %q literal.
local function autoload(feature)
  local okDir, dir = pcall(lfs.writedir)
  if not okDir or type(dir) ~= 'string' or dir == '' then
    logError(feature, 'auto-load: write directory unknown')
    return
  end
  local name = 'Scripts/Overlord/' .. feature.file
  local source, err = readBounded(dir .. name, MAX_COMPANION_FILE, name)
  if source and source:byte(1) == 27 then source, err = nil, name .. ': precompiled chunk refused' end
  if not source then
    logError(feature, 'auto-load: ' .. err)
    return
  end
  local module = feature.module
  local code = 'if ' .. module .. ' ~= nil then return "present" end'
    .. ' if type(loadstring) ~= "function" then return "unavailable" end'
    .. ' local chunk = loadstring(' .. string.format('%q', source) .. ', ' .. string.format('%q', '=' .. name) .. ')'
    .. ' if not chunk then return "syntax" end'
    .. ' if not pcall(chunk) then return "runtime" end'
    .. ' return type(' .. module .. ') == "table" and type(' .. module .. '.bridge) == "function"'
    .. ' and "loaded" or "missing"'
  local result, ok = net.dostring_in(hook.cfg.state, code)
  if ok == false then
    logError(feature, 'auto-load: mission rejected the loader')
  elseif result == 'loaded' then
    log.write('OVERLORD', log.INFO, 'Overlord bridge [' .. feature.name .. ']: auto-loaded ' .. name)
  elseif result == 'present' then
    log.write('OVERLORD', log.INFO, 'Overlord bridge [' .. feature.name .. ']: mission defines ' .. module
      .. '; auto-load skipped')
  else
    logError(feature, 'auto-load: ' .. (AUTOLOAD_RESULTS[result] or 'unexpected loader result'))
  end
end

local function exchange(feature)
  if feature.loadDue then
    feature.loadDue, feature.loadTried = false, true
    autoload(feature)
  end
  local args = string.format('%q', hook.epoch) .. ',' .. string.format('%q', feature.inbound)
  if feature.radioContext then
    hook.radioContext = hook.radioContext or encodedRadioContext()
    args = args .. ',' .. string.format('%q', hook.radioContext)
  end
  local code = 'return ' .. feature.module .. ' and ' .. feature.module .. '.bridge(' .. args .. ')'
  feature.inbound = ''
  local result, ok = net.dostring_in(hook.cfg.state, code)
  if ok == false then
    logError(feature, 'mission entry point failed')
    return
  end
  if type(result) ~= 'string' or result:sub(1, 1) ~= '{' or #result > MAX_SNAPSHOT then
    -- Companion script not loaded in this mission: auto-load it before the next exchange.
    if hook.cfg.autoLoad and not feature.loadTried then feature.loadDue = true end
    return
  end
  start(feature, result)
end

-- One feature step; a Lua error in it is contained so other features keep running.
local function step(feature, now)
  if feature.inflight then
    pump(feature, now)
  elseif now >= feature.nextAt then
    feature.nextAt = now + hook.cfg.interval
    exchange(feature)
  end
end

local function resetFeatures()
  for _, feature in ipairs(hook.features) do
    close(feature, nil)
    feature.inbound, feature.nextAt, feature.loadDue, feature.loadTried = '', 0, false, false
  end
  hook.radioContext = nil   -- re-read per mission/theatre
end

local callbacks = {}

function callbacks.onSimulationStart()
  resetFeatures()
  hook.epoch = string.format('%d-%09d', os.time(), math.random(0, 999999999))
end

function callbacks.onSimulationStop()
  resetFeatures()
  hook.epoch = nil
end

-- Lua runtime errors name identifiers, never values: keep only that phrase, bounded.
local function internalError(err)
  local what = type(err) == 'string' and err:match("attempt to [%w _'%(%)]+")
  return 'internal error' .. (what and (': ' .. what:sub(1, 120)) or '')
end

function callbacks.onSimulationFrame()
  if not hook.cfg or not hook.epoch or not DCS.isServer() then return end
  local now = DCS.getRealTime()
  for _, feature in ipairs(hook.features) do
    local ok, err = pcall(step, feature, now)
    if not ok then close(feature, internalError(err)) end
  end
end

math.randomseed(os.time())
local cfg, err = loadConfig(lfs.writedir() .. 'Config/overlord-hook.lua')
if cfg then
  hook.cfg = cfg
  for _, name in ipairs(cfg.features) do
    local spec = FEATURES[name]
    hook.features[#hook.features + 1] = { name = name, module = spec.module, path = spec.path, file = spec.file,
                                          radioContext = spec.radioContext == true, inbound = '', inflight = nil,
                                          nextAt = 0, lastError = nil, loadDue = false, loadTried = false }
  end
  log.write('OVERLORD', log.INFO, 'Overlord bridge hook loaded for ' .. cfg.host .. ':' .. cfg.port
    .. ' (' .. table.concat(cfg.features, ', ') .. (cfg.autoLoad and '; auto-load' or '') .. ')')
else
  log.write('OVERLORD', log.WARNING, 'Overlord bridge hook disabled: ' .. err)
end
DCS.setUserCallbacks(callbacks)
