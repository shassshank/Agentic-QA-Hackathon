"""Generic questions derived solely from criteria and visible wording."""
import re

PASS_THRESHOLD = .85
FAIL_THRESHOLD = .15


def wording_question(criterion):
    if re.search(r'direction|credit|debit', criterion, re.I):
        return 'direction_shown', dict(type='noul', instructions=(
            'Look at `item.visible_text`. Does the row show which way the money moved? For a deposit or '
            'withdrawal, `amount_text` must show whether money came in or went out, by a sign (+, -, −) or words '
            'such as in/out/credit/debit. For a transfer, that counts too, and so does `account` naming the source '
            "and destination accounts (for example 'Checking to Savings'). A bare amount like '$10.00' with no "
            'sign, no in/out wording and no from/to accounts does not.'))
    if re.search(r'explanation|rejection|error|failure', criterion, re.I):
        return 'explains', dict(type='noul', instructions=(
            'A form submission was rejected. Does the visible text in `item.visible_text` tell the user '
            'what is wrong or what to do to fix it (a missing field, an invalid value, or a broken rule)? '
            "Generic text such as 'Error' or 'Something went wrong' does not."))
    return 'meets', dict(type='noul', instructions=(
        'Does the visible text in `item.visible_text` satisfy this criterion: `item.criterion`? '
        'Judge only the visible text.'))


def direction_contradiction(visible):
    if not isinstance(visible, dict):
        return False
    text = str(visible.get('amount_text', '')).lower()
    kind = str(visible.get('type', '')).lower()
    incoming = '+' in text or bool(re.search(r'\b(in|credit)\b', text))
    outgoing = bool(re.search(r'[-−]|\b(out|debit)\b', text))
    return ('deposit' in kind and outgoing) or ('withdrawal' in kind and incoming)


# condition_not_met competes with violates so a conditional requirement ("if a transfer is rejected, explain it")
# is not failed when the observations show the condition never happened (the transfer was accepted); a separate
# premise noul was unreliable in trials (it scored a real unconditional defect 0.86).
LINKAGE = dict(type='choice', instructions=(
    'Taking `item.failing_observations` as true, how do they relate to the requirement in `item.requirement` '
    '(with `item.assertions`)?'),
    criteria={'violates': 'The observations show the application doing what the requirement forbids, or failing to '
                          'do what it requires, in a situation the requirement covers.',
              'condition_not_met': 'The requirement only applies in a specific situation (for example when a transfer '
                                   'is rejected), and the observations show that situation never happened (for example '
                                   'the transfer was accepted instead), so this requirement is not what failed.',
              'different_behaviour': 'The observations concern a different behaviour than the requirement describes.',
              'unsure': 'Cannot tell from the observations.'})
NOT_VIOLATED = ('condition_not_met', 'different_behaviour')
