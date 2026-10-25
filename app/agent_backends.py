"""Optional Omarchy CLI adapters. Preserve each agent's own configuration.

Claude keeps Void's native implementation. Other backends have separate bounded
conversation context and never receive Claude's session ID or permission flags.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

HISTORY = {}

def available():
    return [{'id': 'claude', 'name': 'Claude Code', 'available': bool(shutil.which('claude'))},
            {'id': 'codex', 'name': 'Codex', 'available': bool(shutil.which('codex'))},
            {'id': 'hermes', 'name': 'Hermes · Field', 'available': bool(shutil.which('hermes'))}]

def run(agent, prompt, emit, state, trust):
    if agent not in ('codex', 'hermes'):
        emit(t='error', text='Unknown agent backend'); return 'done'
    binary = shutil.which(agent)
    if not binary:
        emit(t='error', text=f'{agent} is not installed'); return 'done'
    key = (agent, state.cwd)
    context = HISTORY.get(key, [])[-6:]
    full = ('Previous conversation with this agent:\n' + '\n'.join(context) + '\n\n' if context else '') + prompt
    if agent == 'codex':
        args = [binary, 'exec', '--json', '--skip-git-repo-check', '--sandbox',
                'read-only' if trust == 'readonly' else 'workspace-write', '-']
    else:
        if trust == 'readonly':
            emit(t='error', text='Void cannot enforce read-only mode for Hermes. Use Claude or Codex, or change the setting; Hermes uses its own approval policy.')
            return 'done'
        args = [binary, 'chat', '--query-file', '-', '--oneshot', '--quiet', '--cli']
    emit(t='init', model=agent)
    emit(t='note', text=f'{agent} uses its existing account and configuration.')
    proc = subprocess.Popen(args, cwd=state.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    state.proc = proc
    errors = []
    def drain():
        for line in proc.stderr:
            errors.append(line)
            if len(errors) > 80: del errors[:40]
    thread = threading.Thread(target=drain, daemon=True); thread.start()
    answer = []
    try:
        proc.stdin.write(full); proc.stdin.close()
        for line in proc.stdout:
            if agent == 'codex':
                try: event = json.loads(line)
                except ValueError: continue
                item = event.get('item') or {}
                if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
                    text = item.get('text', '')
                    answer.append(text); emit(t='delta', text=text+'\n')
                elif event.get('type') in ('error', 'turn.failed'):
                    err = event.get('error') or {}
                    errors.append(str(event.get('message') or err.get('message') or 'Codex failed'))
            else:
                # Quiet CLI output is final-response text, not the tool trace.
                answer.append(re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', line))
        code = proc.wait(); thread.join(timeout=2)
        if state.stopped:
            emit(t='stopped'); return 'done'
        result = ''.join(answer).strip() if agent == 'hermes' else '\n'.join(answer).strip()
        if code:
            emit(t='error', text=''.join(errors)[-1200:] or f'{agent} exited with code {code}')
        elif not result:
            emit(t='error', text=f'{agent} returned no response')
        else:
            HISTORY[key] = (context + ['User: '+prompt[-6000:], 'Assistant: '+result[-6000:]])[-6:]
            emit(t='result', text=result, is_error=False)
        return 'done'
    finally:
        state.proc = None
