"""Human catalog identity correlation shared by mission-backed radio controllers."""
import re

from . import domain

# A whole callsign token: DCC unit names embed UUIDs ("...-244f4688-...") that must not read as "f 4 688".
_CALLSIGN = re.compile(r'(?<![A-Za-z0-9])([A-Za-z]{2,}(?: [A-Za-z]+)?)[ _-]?(\d)[ -]?(1\d|\d)(?!\d)')


def spoken_callsigns(names, limit=32):
    """STT priming: callsigns as said on the radio ("Silver 1-2" -> "Silver one two").

    Whisper otherwise writes an unprimed "two" as "to" and drops the element digit.
    """
    spoken = set()
    for text in names:
        match = _CALLSIGN.search(text or '')
        if match:
            spoken.add(f"{match[1]} {domain._ONES[int(match[2])]} {domain._ONES[int(match[3])]}")
    return sorted(spoken)[:limit]


def identify_player(players, parsed, coalition):
    """Return (catalog player, spoken caller, problem), never guessing between candidates."""
    catalog = [player for player in players if player['coalition'] == coalition]
    if parsed['modex']:
        caller = (' '.join(domain._ONES[int(digit)] for digit in parsed['modex'])
                  if parsed['modex'].isdigit() else parsed['modex'])
        matches = [player for player in catalog if player['modex'] and player['modex'] == parsed['modex']]
        other = 'callsign'
    else:
        heard = parsed['transmitter']
        if not heard or heard.get('group') is None or heard['flight'] < 0 or heard['element'] < 0:
            return None, domain.UNKNOWN_CALLER, 'say again your callsign'
        caller = f"{heard['group']} {heard['flight']} {heard['element']}"
        matches = [player for player in catalog if domain.callsign_matches(
            {'pilot': player['player_name'], 'callsign': player['callsign']},
            heard['group'], heard['flight'], heard['element'])]
        other = 'modex'
    if len(matches) > 1:
        return None, caller, f'more than one aircraft with that callsign, say your {other}'
    if not matches:
        return None, caller, 'unable, no aircraft with that callsign'
    return matches[0], caller, None


def _word_in(needle, haystack):
    return bool(needle) and re.search(rf'(?<![a-z0-9]){re.escape(needle.lower())}(?![a-z0-9])', haystack) is not None


def sender_identifies(players, sender, player):
    """Require one positive human match, rejecting generic or contradictory SRS names.

    SRS names are user-chosen: this is identity correlation, not authentication.
    """
    text = ' '.join(sender.lower().split())
    if not text:
        return False
    catalog = [candidate for candidate in players if candidate['coalition'] == player['coalition']]
    heard = [(group, int(flight), int(element))
             for group, flight, element in re.findall(r'([a-z]+) ?(\d)-?(\d{1,2})(?!\d)', text)]
    by_name = [candidate for candidate in catalog if _word_in(candidate['player_name'], text)]
    if not heard:
        return by_name == [player]
    by_callsign = [candidate for candidate in catalog if any(domain.callsign_matches(
        {'pilot': candidate['player_name'], 'callsign': candidate['callsign']}, *callsign) for callsign in heard)]
    mine = {'pilot': player['player_name'], 'callsign': player['callsign']}
    return (all(domain.callsign_matches(mine, *callsign) for callsign in heard)
            and by_callsign == [player] and (not by_name or player in by_name))
