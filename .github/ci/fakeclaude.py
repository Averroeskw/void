"""Stand-in for `claude -p --output-format stream-json` on the Windows test machine."""
import json, sys
sys.stdin.read()
def out(o): print(json.dumps(o), flush=True)
out({"type": "system", "subtype": "init", "session_id": "ci"})
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "dir", "description": "List files"}}]}})
out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "app  install.ps1"}]}})
text = "[proud] Hi from Windows! Everything works — even ünïcödé ✓."
out({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
out({"type": "result", "subtype": "success", "session_id": "ci", "result": text})
