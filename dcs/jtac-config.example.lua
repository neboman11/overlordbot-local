--[[
Overlord JTAC mission configuration (example).

Load this file with a MISSION START trigger "DO SCRIPT FILE" before
overlord_jtac.lua.  It becomes part of the .miz that every client downloads,
so it must never contain secrets: the bridge and API tokens belong only in the
server-only hook configuration described at the top of overlord_hook.lua.

Everything the JTAC says about a task comes from here or from live DCS state.
Replace each value with what you authored in the mission; unit, group and static
names must match the mission editor exactly.  Omit a brief line you do not have:
the controller then refuses to brief that task instead of inventing the line.

Field reference (per task):
  id            1-99, spoken as "task <n>"
  name          up to 40 characters
  coalition     "blue" or "red": the side whose pilots may request the task
  controller    name of the live unit that lases/marks (ground unit or drone)
  target        exactly one of { group = "..." } { unit = "..." } { static = "..." }
  brief         type 1-3, ip (40 chars), heading (0-360 deg magnetic), offset (20),
                distance (nm), elevation (feet), description (120), location (60),
                mark (60), friendlies (80), egress (60), remarks (160, optional)
  Control types select simplified simulation phraseology, not full doctrinal or
  visual-acquisition certification. location is an authored fixed reference/area,
  not a dynamically computed live grid; author moving-target briefs accordingly.
  laserCode     1111-1788 (first digit 1, second 1-7, others 1-8)
  marks         any of "laser", "smoke", "ir", "illumination" the controller may use
  smokeColor    green, red, white, orange or blue (default white)
  readback      brief keys the pilot must read back, e.g. { "elevation", "location", "remarks" }
  attackHeading optional { min, max } inbound heading window required for clearance
  requireMark true: clearance waits for acknowledged mission marking/live spot
]]

OverlordJTACConfig = {
  leaseSeconds = 15,       -- spots expire without fresh accepted brain replies (5-60)
  laserUpdateSeconds = 0.5, -- moving-target laser update and safety check period (0.1-2)
  tasks = {
    {
      id = 1,
      name = 'armor column',
      coalition = 'blue',
      controller = 'JTAC-1 Reaper',
      target = { group = 'Red Armor Column' },
      brief = {
        type = 2,
        ip = 'IP Dog',
        heading = 270,
        offset = 'offset left',
        distance = 6.5,
        elevation = 450,
        description = 'three armored vehicles on the road',
        location = 'grid kilo mike one two three four five six',
        mark = 'laser code one six eight eight',
        friendlies = 'south three kilometers',
        egress = 'north to IP Cat',
        remarks = 'final attack heading two four zero to three zero zero',
      },
      laserCode = 1688,
      marks = { 'laser', 'smoke' },
      smokeColor = 'white',
      readback = { 'elevation', 'location', 'remarks' },
      attackHeading = { 240, 300 },
      requireMark = true,
    },
  },
}
