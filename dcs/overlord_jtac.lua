--[[
Overlord JTAC mission companion (mission scripting environment).

Load order in the mission editor (MISSION START triggers, "DO SCRIPT FILE"):
  1. your copy of jtac-config.example.lua (defines OverlordJTACConfig)
  2. overlord_jtac.lua (this file)

Runs inside the normal sanitized mission environment: no sockets, files or
secrets.  The server hook (Scripts/Hooks/overlord_hook.lua) calls exactly one
entry point, OverlordJTAC.bridge(epoch, inbound_json), about once a second and
relays the returned JSON snapshot to the Overlord brain; JSON goes through
OverlordJSON.encode/decode, the engine codec the hook installs first.  Commands from the brain
are limited to laser_on, laser_off and mark for tasks authored in the config;
each is executed through the DCS API (Spot.createLaser, Spot:setPoint,
Spot:destroy, Spot.createInfraRed, trigger.action.smoke,
trigger.action.illuminationBomb) and acknowledged with its actual result.

Safety: every spot is destroyed when its target unit or controller dies, line of
sight is lost, the brain lease expires (no accepted brain response echoing this
script's latest snapshot sequence for leaseSeconds of mission time), the epoch
changes, the mission ends, or OverlordJTAC.stop() is called.
]]

if OverlordJTAC and OverlordJTAC.stop then
  OverlordJTAC.stop()
end

OverlordJTAC = (function()
  local M = {}
  local MAX_INBOUND, MAX_COMMANDS, MAX_ACKS = 65536, 32, 128
  local SPOT_OFFSET = { x = 0, y = 2, z = 0 }
  local MARKS = { laser = true, smoke = true, ir = true, illumination = true }
  local BRIEF_LIMITS = { ip = 40, offset = 20, description = 120, location = 60, mark = 60,
                         friendlies = 80, egress = 60, remarks = 160 }
  local READBACK = { ip = true, heading = true, distance = true, elevation = true, description = true,
                     location = true, mark = true, friendlies = true, egress = true, remarks = true }
  local SMOKE = { green = 0, red = 1, white = 2, orange = 3, blue = 4 }
  local COALITION = { red = 1, blue = 2 }

  local cfg = OverlordJTACConfig or {}
  local lease = 15
  local interval = 0.5
  local tasks, order = {}, {}
  local errors = {}
  local state = { epoch = nil, seq = 0, last = nil, acks = {}, ackOrder = {}, running = true }

  local function fail(message)
    if #errors < 8 then errors[#errors + 1] = message end
    env.error('Overlord JTAC: ' .. message)
  end

  local function text(value, limit)
    return type(value) == 'string' and #value >= 1 and #value <= limit and not value:find('%c')
  end

  local function number(value, lo, hi, integer)
    return type(value) == 'number' and value == value and value >= lo and value <= hi
      and (not integer or value == math.floor(value))
  end

  local function validLaserCode(code)
    if not number(code, 1111, 1788, true) then return false end
    local s = tostring(code)
    return s:sub(1, 1) == '1' and s:sub(2, 2):match('[1-7]') ~= nil and s:sub(3):match('^[1-8][1-8]$') ~= nil
  end

  local function copyBrief(raw, id)
    local brief = {}
    if raw == nil then return brief end
    if type(raw) ~= 'table' then return nil end
    for key, value in pairs(raw) do
      local ok
      if key == 'type' then ok = number(value, 1, 3, true)
      elseif key == 'heading' then ok = number(value, 0, 360, true)
      elseif key == 'distance' then ok = number(value, 0, 100, false)
      elseif key == 'elevation' then ok = number(value, -1500, 30000, true)
      elseif BRIEF_LIMITS[key] then ok = text(value, BRIEF_LIMITS[key])
      else ok = false end
      if not ok then
        fail('task ' .. id .. ' brief field ' .. tostring(key) .. ' invalid')
        return nil
      end
      brief[key] = value
    end
    return brief
  end

  local function exists(object)
    if not object then return false end
    local ok, result = pcall(function() return object:isExist() and object:getLife() >= 1 end)
    return ok and result == true
  end

  -- Configuration: invalid tasks are skipped and reported in the snapshot error.
  for index, raw in ipairs(type(cfg.tasks) == 'table' and cfg.tasks or {}) do
    local id = type(raw) == 'table' and raw.id
    local target = type(raw) == 'table' and raw.target
    if not number(id, 1, 99, true) or tasks[id] then
      fail('task #' .. index .. ' needs a unique id 1-99')
    elseif not text(raw.name, 40) or not COALITION[raw.coalition] or not text(raw.controller, 64)
        or type(target) ~= 'table' then
      fail('task ' .. id .. ' needs name, coalition red/blue, controller and target')
    elseif (target.group and 1 or 0) + (target.unit and 1 or 0) + (target.static and 1 or 0) ~= 1
        or not text(target.group or target.unit or target.static, 64) then
      fail('task ' .. id .. ' target needs exactly one of group, unit, static')
    else
      local task = { id = id, name = raw.name, coalition = COALITION[raw.coalition], controller = raw.controller,
                     target = { group = target.group, unit = target.unit, static = target.static },
                     brief = copyBrief(raw.brief, id), marks = {}, readback = {},
                     requireMark = raw.requireMark == true, smoke = SMOKE[raw.smokeColor or 'white'] }
      local ok = task.brief ~= nil and task.smoke ~= nil
      for _, kind in ipairs(type(raw.marks) == 'table' and raw.marks or {}) do
        if MARKS[kind] then task.marks[#task.marks + 1] = kind else ok = false end
      end
      for _, key in ipairs(type(raw.readback) == 'table' and raw.readback or {}) do
        if READBACK[key] then task.readback[#task.readback + 1] = key else ok = false end
      end
      if raw.laserCode ~= nil then
        if validLaserCode(raw.laserCode) then task.laserCode = raw.laserCode else ok = false end
      end
      if raw.attackHeading ~= nil then
        local h = raw.attackHeading
        if type(h) == 'table' and number(h[1], 0, 360, true) and number(h[2], 0, 360, true) then
          task.attackHeading = { h[1], h[2] }
        else ok = false end
      end
      if ok then
        tasks[id] = task
        order[#order + 1] = id
      else
        fail('task ' .. id .. ' has an invalid marks, readback, laserCode, smokeColor, attackHeading or brief')
      end
    end
  end
  table.sort(order)
  if type(cfg.leaseSeconds) == 'number' and cfg.leaseSeconds >= 5 and cfg.leaseSeconds <= 60 then
    lease = cfg.leaseSeconds
  end
  if type(cfg.laserUpdateSeconds) == 'number' and cfg.laserUpdateSeconds >= 0.1 and cfg.laserUpdateSeconds <= 2 then
    interval = cfg.laserUpdateSeconds
  end

  local function nameOf(object)
    local ok, name = pcall(function() return object:getName() end)
    return ok and type(name) == 'string' and name or nil
  end

  -- Target elements are the objects named at load time; "destroyed" needs affirmative
  -- death evidence for every one of them, never just a failed lookup.
  for _, id in ipairs(order) do
    local task = tasks[id]
    task.elements, task.dead, task.total = {}, {}, 0
    local objects = {}
    if task.target.group then
      local group = Group.getByName(task.target.group)
      objects = group and group:getUnits() or {}
    elseif task.target.unit then
      objects = { Unit.getByName(task.target.unit) }
    else
      objects = { StaticObject.getByName(task.target.static) }
    end
    for _, object in ipairs(objects) do
      local name = object and nameOf(object)
      if name and not task.elements[name] then
        task.elements[name] = true
        task.total = task.total + 1
      end
    end
  end

  local function recordDeath(object)
    local name = object and nameOf(object)
    if not name then return end
    for _, id in ipairs(order) do
      if tasks[id].elements[name] then tasks[id].dead[name] = true end
    end
  end

  local function destroyedCount(task)
    local count = 0
    for _ in pairs(task.dead) do count = count + 1 end
    return count
  end

  -- Live state ------------------------------------------------------------

  local function observe(task, object, out)
    if exists(object) then
      out[#out + 1] = object
    elseif object then
      -- An object that still exists with no life left is affirmatively dead.
      local ok, dead = pcall(function() return object:isExist() and object:getLife() < 1 end)
      if ok and dead then recordDeath(object) end
    end
  end

  local function targetUnits(task)
    local out = {}
    if task.target.group then
      local group = Group.getByName(task.target.group)
      if group and group:isExist() then
        for _, unit in ipairs(group:getUnits() or {}) do observe(task, unit, out) end
      end
    elseif task.target.unit then
      observe(task, Unit.getByName(task.target.unit), out)
    else
      observe(task, StaticObject.getByName(task.target.static), out)
    end
    return out
  end

  local function controllerUnit(task)
    local unit = Unit.getByName(task.controller)
    if exists(unit) and unit:getCoalition() == task.coalition then return unit end
    return nil
  end

  local function eye(unit)
    local p = unit:getPoint()
    return { x = p.x, y = p.y + SPOT_OFFSET.y, z = p.z }
  end

  local function aim(object)
    local p = object:getPoint()
    return { x = p.x, y = p.y + 1, z = p.z }
  end

  local function destroySpot(task, kind)
    local spot = task[kind]
    task[kind] = nil
    if spot then pcall(function() spot.spot:destroy() end) end
  end

  local function destroyAll()
    for _, id in ipairs(order) do
      destroySpot(tasks[id], 'laser')
      destroySpot(tasks[id], 'ir')
    end
  end

  -- Keeps spots on their moving target and drops them when anything is lost.
  local function watchdog(_, now)
    if not state.running then return nil end
    local expired = state.last == nil or now - state.last > lease
    for _, id in ipairs(order) do
      local task = tasks[id]
      for _, kind in ipairs({ 'laser', 'ir' }) do
        local spot = task[kind]
        if spot then
          local controller = controllerUnit(task)
          if expired or not controller or not exists(spot.target) then
            destroySpot(task, kind)
          else
            local point = aim(spot.target)
            if not land.isVisible(eye(controller), point) then
              destroySpot(task, kind)
            elseif not pcall(function() spot.spot:setPoint(point) end) then
              destroySpot(task, kind)
            end
          end
        end
      end
    end
    return now + interval
  end

  -- Commands ------------------------------------------------------------------

  local function remember(id, status, reason)
    if not state.acks[id] then
      state.ackOrder[#state.ackOrder + 1] = id
      if #state.ackOrder > MAX_ACKS then
        state.acks[table.remove(state.ackOrder, 1)] = nil
      end
    end
    state.acks[id] = { id = id, status = status, reason = reason }
    return state.acks[id]
  end

  local function validId(id)
    return type(id) == 'string' and #id >= 1 and #id <= 64 and not id:find('[^%w%._:%-]')
  end

  local function sighted(task)
    local controller = controllerUnit(task)
    if not controller then return nil, nil, 'controller not available' end
    local targets = targetUnits(task)
    if #targets == 0 then return nil, nil, 'target not active' end
    local origin = eye(controller)
    for _, target in ipairs(targets) do
      if land.isVisible(origin, aim(target)) then return controller, target end
    end
    return nil, nil, 'no line of sight'
  end

  local function allowed(task, kind)
    for _, mark in ipairs(task.marks) do
      if mark == kind then return true end
    end
    return false
  end

  local function execute(command)
    local task = type(command.task) == 'number' and tasks[command.task] or nil
    if not task then return 'rejected', 'unknown task' end
    if type(command.deadline) ~= 'number' or timer.getTime() > command.deadline then
      return 'expired', 'deadline passed'
    end
    if command.op == 'laser_off' then
      destroySpot(task, 'laser')
      destroySpot(task, 'ir')
      return 'done'
    end
    local kind = command.op == 'laser_on' and 'laser' or command.op == 'mark' and command.kind or nil
    if not kind or kind == 'laser' and command.op ~= 'laser_on' or not allowed(task, kind) then
      return 'rejected', 'mark not authorised for task'
    end
    if kind == 'laser' and not task.laserCode then return 'rejected', 'no laser code' end
    -- Every positive action needs the live controller before any side effect.
    if not controllerUnit(task) then return 'failed', 'controller not available' end
    if kind == 'smoke' or kind == 'illumination' then
      local targets = targetUnits(task)
      if #targets == 0 then return 'failed', 'target not active' end
      local p = targets[1]:getPoint()
      local ok = pcall(function()
        if kind == 'smoke' then
          trigger.action.smoke({ x = p.x, y = land.getHeight({ x = p.x, y = p.z }), z = p.z }, task.smoke)
        else
          trigger.action.illuminationBomb({ x = p.x, y = land.getHeight({ x = p.x, y = p.z }) + 600, z = p.z }, 1000000)
        end
      end)
      if not ok then return 'failed', 'dcs api error' end
      return 'done'
    end
    local controller, target, reason = sighted(task)
    if not controller then return 'failed', reason end
    destroySpot(task, kind)
    local ok, spot = pcall(function()
      if kind == 'laser' then
        return Spot.createLaser(controller, SPOT_OFFSET, aim(target), task.laserCode)
      end
      return Spot.createInfraRed(controller, SPOT_OFFSET, aim(target))
    end)
    if not ok or not spot then return 'failed', 'dcs api error' end
    task[kind] = { spot = spot, target = target, command = command.id }
    return 'done'
  end

  local function apply(message, report)
    local cancel = type(message.cancel) == 'table' and message.cancel or {}
    for index = 1, math.min(#cancel, MAX_COMMANDS) do
      local id = cancel[index]
      if validId(id) then
        report[#report + 1] = state.acks[id] or remember(id, 'cancelled', 'cancelled before execution')
      end
    end
    local commands = type(message.commands) == 'table' and message.commands or {}
    for index = 1, math.min(#commands, MAX_COMMANDS) do
      local command = commands[index]
      if type(command) == 'table' and validId(command.id) then
        local ack = state.acks[command.id]
        if not ack then
          local status, reason = execute(command)
          ack = remember(command.id, status, reason)
        end
        report[#report + 1] = ack
      end
    end
  end

  local function spotState(spot)
    return { on = spot ~= nil, command = spot and spot.command or nil }
  end

  local function snapshot(report)
    local out = { version = 1, epoch = state.epoch, seq = state.seq, time = timer.getTime(), tasks = {},
                  acks = report }
    if #errors > 0 then out.error = table.concat(errors, '; '):sub(1, 240) end
    for _, id in ipairs(order) do
      local task = tasks[id]
      local controller = controllerUnit(task)
      local targets = targetUnits(task)
      local dead = destroyedCount(task)
      local target = { alive = #targets > 0, total = math.max(task.total, #targets + dead), remaining = #targets,
                       destroyed = #targets == 0 and task.total > 0 and dead == task.total }
      if #targets > 0 then
        target.lat, target.lon, target.alt = coord.LOtoLL(targets[1]:getPoint())
      end
      out.tasks[#out.tasks + 1] = {
        id = id, name = task.name, coalition = task.coalition,
        controller = { name = task.controller, alive = controller ~= nil },
        target = target, brief = task.brief, laser_code = task.laserCode, marks = task.marks,
        readback = task.readback, attack_heading = task.attackHeading, require_mark = task.requireMark,
        laser = spotState(task.laser), ir = spotState(task.ir),
      }
    end
    return out
  end

  -- Hook entry point ----------------------------------------------------------

  -- The lease (state.last) is renewed only by an accepted brain response for this
  -- epoch that echoes the sequence of the snapshot this script issued last.  Empty,
  -- malformed, rejected, previous-epoch or replayed responses renew nothing and run
  -- no commands, so an offline brain lets every spot expire after leaseSeconds.
  function M.bridge(epoch, inbound)
    if not state.running or not validId(epoch) then return nil end
    if epoch ~= state.epoch then
      destroyAll()
      state.epoch, state.seq, state.acks, state.ackOrder, state.last = epoch, 0, {}, {}, nil
    end
    local report = {}
    if state.seq > 0 and type(inbound) == 'string' and #inbound > 0 and #inbound <= MAX_INBOUND then
      local ok, message = pcall(OverlordJSON.decode, inbound)
      if ok and type(message) == 'table' and message.version == 1 and message.accepted == true
          and message.epoch == state.epoch and message.seq == state.seq then
        state.last = timer.getTime()
        apply(message, report)
      end
    end
    state.seq = state.seq + 1
    return OverlordJSON.encode(snapshot(report))
  end

  function M.stop()
    state.running = false
    destroyAll()
  end

  world.addEventHandler({
    onEvent = function(_, event)
      if not event then return end
      if event.id == world.event.S_EVENT_MISSION_END then
        M.stop()
      elseif event.id ~= nil and event.id == world.event.S_EVENT_DEAD then
        recordDeath(event.initiator)
      elseif event.id ~= nil and event.id == world.event.S_EVENT_KILL then
        recordDeath(event.target)
      end
    end,
  })
  timer.scheduleFunction(watchdog, nil, timer.getTime() + interval)
  env.info('Overlord JTAC: ' .. #order .. ' task(s) loaded')
  return M
end)()
