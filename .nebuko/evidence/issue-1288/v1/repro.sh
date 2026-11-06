#!/bin/sh
# Oracle for issue #1288: a dev answer comment that QUOTES a bot-agent marker
# in its body makes _caller_agent return "dev\nunknown", so the same-agent
# loop guard (_loop_verdict) treats the self-call as a cross-agent call and
# allows the run -> self-retrigger loop.
#
# Frozen inputs: the quoted-marker body from issue #1288 comment 6657278444
# and a clean answer body (header marker on line 1 only).
cd "$(dirname "$0")"
python3 - <<'PYEOF'
import sys, os, json
sys.path.insert(0, os.path.join(os.getcwd(), '..', '..', '..', '..', 'src'))
sys.path.insert(0, os.environ.get('NEBUKO_SRC', ''))
from nebuko.engine.run_loop import Engine, _caller_agent

quoted = open('quoted_body.txt').read()
clean = open('clean_body.txt').read()

class _E: _loop_verdict = staticmethod(Engine._loop_verdict)
_e = _E()
quoted_caller = _caller_agent(quoted)
quoted_verdict = _e._loop_verdict(quoted, 'dev', '', '')
clean_caller = _caller_agent(clean)
clean_verdict = _e._loop_verdict(clean, 'dev', '', '')

print(json.dumps({
    'quoted_caller': quoted_caller,
    'quoted_skip_agent': quoted_verdict.get('SKIP_AGENT'),
    'clean_caller': clean_caller,
    'clean_skip_agent': clean_verdict.get('SKIP_AGENT'),
    # Counterfactual: read the caller only from the header line (line 2 of an
    # answer comment, top line of a continuation part) instead of every line.
    'header_only_quoted_caller': _caller_agent('\n'.join(quoted.splitlines()[:2])),
}))
PYEOF