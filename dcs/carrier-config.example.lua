--[[
Overlord carrier mission configuration (example, optional).

Load this file with a MISSION START trigger "DO SCRIPT FILE" before
overlord_carrier.lua.  It becomes part of the .miz that every client downloads,
so it must never contain secrets: the bridge and API tokens belong only in the
server-only hook configuration described at the top of overlord_hook.lua.

It holds operational facts only.  It cannot add carriers and has no radio
settings: each carrier's frequency and modulation are read from the ship
unit's own radio settings in the mission editor (in Hz, AM when omitted), and
any frequency/modulation key here is rejected.  Without this file every
carrier is still registered and reachable on its mission radio, but the
controller answers "unable" wherever CASE or weather facts are missing.

Top level:
  leaseSeconds  5-60 (default 15): watched aircraft are dropped when no fresh
                accepted brain response arrives for this long (mission time)
  carriers      table keyed by the exact ship UNIT name from the mission editor

Per carrier (all optional; omit what you do not know):
  recoveryCase   1, 2 or 3: recovery CASE in force for this ship
  altimeterInHg  25-32.5: overrides the sea-level pressure read from DCS
  ceilingFt      0-60000: reported cloud ceiling at the ship
  visibilityNm   0-100: reported visibility at the ship
  profile        deck geometry for carrier types without a built-in profile:
                 "nimitz", "vinson", "forrestal", "essex", "tarawa" or "invincible".
                 Built-in: CVN_71/72/73/75 and Stennis (nimitz), VINSON, Forrestal,
                 Essex, LHA_Tarawa, hms_invincible.  Kuznetsov, ara_vdm and other types
                 report no deck angle unless you choose a matching profile.
]]

OverlordCarrierConfig = {
  leaseSeconds = 15,
  carriers = {
    ['CVN-71 Theodore Roosevelt'] = {
      recoveryCase = 1,
      ceilingFt = 5000,
      visibilityNm = 10,
    },
    ['CVN-74 John C. Stennis'] = {
      recoveryCase = 3,
      altimeterInHg = 29.92,
      ceilingFt = 800,
      visibilityNm = 3,
    },
  },
}
