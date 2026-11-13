--[[
Overlord airfield mission configuration (example, optional).

Load this file with a MISSION START trigger "DO SCRIPT FILE" before
overlord_airfield.lua.  It becomes part of the .miz that every client downloads,
so it must never contain secrets: the bridge and API tokens belong only in the
server-only hook configuration described at the top of overlord_hook.lua.

It holds runway operating facts only.  It cannot add airfields, runways or
radios: airfields are the theatre's native airdromes, runway geometry comes
from DCS, and frequencies come only from the installed terrain's native
Radio.lua (read by the server hook, see airfieldRadioFiles in
overlord_hook.lua).  Any other key, including frequencies, is rejected with an
error.  Without this file every airdrome with valid native data is served; the
controller picks the runway from live wind and gives no pattern direction.

Top level:
  leaseSeconds  5-60 (default 15): watched aircraft are dropped when no fresh
                accepted brain response arrives for this long (mission time)
  airfields     table keyed by the exact native airbase name as DCS reports it

Per airfield (all optional; omit what you do not know):
  runwayNames    corrects or completes native runway labels; value is the real
                 designator of the strip ("05", "13L", ...).  Keys:
                   "04" or "4"   every native runway DCS reports with that label
                   "native:3"    only the 3rd entry of Airbase.getRunways (wins
                                 over a label key; needed when several strips
                                 share one native label, e.g. three parallels)
                 The script's error text names the native index of every runway
                 it rejects ("<field> runway 3: ...", "ambiguous (native 1,2,3)").
                 Labels must still agree with the native course (within 35
                 degrees of the strip's true heading, either end) and L/R must
                 match the strips' native positions, otherwise those runways stay
                 unavailable.  Two parallels sharing an unsuffixed number get L/R
                 automatically from their positions.
  closedRunways  list of designators (either end) that are closed
  activeRunway   designator of an open runway end to use instead of the
                 wind-selected one
  pattern        "left" or "right" traffic pattern direction
]]

OverlordAirfieldConfig = {
  leaseSeconds = 15,
  airfields = {
    ['Batumi'] = {
      pattern = 'left',
    },
    ['Kutaisi'] = {
      activeRunway = '07',
    },
    ['Senaki-Kolkhi'] = {
      closedRunways = { '27' },   -- closes the whole 09/27 strip
    },
    -- Three parallel strips that DCS all reports as "4" (native entries 1-3 here are an
    -- example; read the indices from the script's "ambiguous (native ...)" error):
    -- ['Example Field'] = {
    --   runwayNames = { ['native:1'] = '04L', ['native:2'] = '04C', ['native:3'] = '04R' },
    -- },
  },
}
