#!/bin/sh
# Oracle for issue #1361: an agent answer comment that QUOTES a bot-agent
# marker in its prose makes _caller_agent (run_loop.py:398) return
# "dev\nunknown" instead of "dev", so _loop_verdict treats the self-call as a
# cross-agent call (SKIP_AGENT: false) and the workflow-continue token on
# line 1 re-triggers the same agent.
#
# Frozen inputs: the full body of comment 6657278444 (real answer layout:
# live header line 1 with the workflow-continue token, agent marker line 2,
# quoted marker inside the prose) and a clean answer body.
#
# Counterfactual deltas judge a HEADER-ONLY reading of the caller (the fix
# shape): it returns "dev" on the quoted body, while a cross-agent header
# still reads as the header's role. The cross-agent control rewrites the
# header marker to architect on the same quoted body.
cd "$(dirname "$0")"
python3 - <<'PYEOF'
import sys, os, json
sys.path.insert(0, os.path.join(os.getcwd(), '..', '..', '..', '..', 'src'))
sys.path.insert(0, os.environ.get('NEBUKO_SRC', ''))
from nebuko.engine.run_loop import Engine, _caller_agent

quoted = open('quoted_body.txt').read()
clean = open('clean_body.txt').read()

def verdict(body, task):
    return Engine._loop_verdict(body, task, '', '')

# Cross-agent control: same quoted body but the header names architect.
cross = quoted.replace('<!-- bot-agent-dev -->', '<!-- bot-agent-architect -->', 1)

print(json.dumps({
    'quoted_caller': _caller_agent(quoted),
    'quoted_skip_agent': verdict(quoted, 'dev').get('SKIP_AGENT'),
    # Counterfactual: caller read only from the header region (line 2 of an
    # answer comment = format_answer_header's marker line).
    'header_only_quoted_caller': _caller_agent('\n'.join(quoted.splitlines()[:2])),
    'header_only_cross_caller': _caller_agent('\n'.join(cross.splitlines()[:2])),
    # Cross-agent control: full-body reading on the architect-headed body.
    'cross_full_caller': _caller_agent(cross),
    # Clean answer body: no quoted marker, current code already healthy.
    'clean_caller': _caller_agent(clean),
}))
PYEOF