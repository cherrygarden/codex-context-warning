#!/usr/bin/env python3
"""Advisory Codex hook. Reads token metadata only, never copies chat text."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def usage(path):
    if not path:
        return None
    # Bounded tail: no full-transcript scan on every turn.
    with Path(path).open('rb') as stream:
        stream.seek(0, 2)
        end = stream.tell()
        stream.seek(max(0, end - 2_000_000))
        lines = stream.read().splitlines()
    for line in reversed(lines):
        if b'"token_count"' not in line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        payload = row.get('payload', {})
        if row.get('type') != 'event_msg' or payload.get('type') != 'token_count':
            continue
        info = payload.get('info') or {}
        used = (info.get('last_token_usage') or {}).get('total_tokens')
        window = info.get('model_context_window')
        if (type(used) is int and type(window) is int
                and used >= 0 and window > 0):
            return 100 * used / window
        return None
    return None


def handle(data, root=ROOT):
    sid = data.get('session_id')
    event = data.get('hook_event_name')
    if not sid or data.get('agent_id') or event not in ('Stop', 'PreCompact', 'SessionStart'):
        return {}
    if event == 'Stop' and data.get('stop_hook_active'):
        return {}
    if event == 'SessionStart' and data.get('source') != 'compact':
        return {}
    key = hashlib.sha256(str(sid).encode()).hexdigest()[:20]
    directory = root / 'progress' / 'context'
    statefile = directory / (key + '.json')
    note = directory / (key + '-handoff.md')
    state = json.loads(statefile.read_text()) if statefile.exists() else {}
    percent = usage(data.get('transcript_path')) if event == 'Stop' else None
    if event == 'Stop' and (state.get('notified') or percent is None or percent < 50):
        return {}
    instruction = (
        f'Сохрани или обнови точку продолжения в {note}: цель и критерий готовности, '
        'принятое/отклонённое, открытое, пути к артефактам и уликам, проверки, '
        'границы и следующий конкретный шаг. Только проверенные решения, '
        'без секретов, личных данных и копирования переписки. Коротко предупреди '
        'пользователя, что стоит продолжить в новом чате, и дай ссылку на заметку. '
        'Не создавай чат без просьбы пользователя, не прерывай безопасную работу и не закрывай '
        'незавершённую задачу. Если запись не удалась, честно сообщи ограничение'
    )
    directory.mkdir(parents=True, exist_ok=True)
    if event == 'PreCompact':
        state['compact_pending'] = True
        output = {'systemMessage': 'Контекст сжимается. После сжатия агенту будет передано поручение сохранить точку продолжения'}
    elif event == 'SessionStart':
        state['compact_pending'] = False
        state['notified'] = True
        output = {'hookSpecificOutput': {'hookEventName': event,
                  'additionalContext': 'Контекст был сжат. ' + instruction}}
    else:
        state['notified'] = True
        state['percent'] = round(percent, 1)
        output = {'decision': 'block', 'reason':
                  f'По метрикам последнего запроса использовано около {percent:.0f}% окна контекста. ' + instruction}
    # Atomic state update; no transcript contents or user identifiers stored.
    temporary = statefile.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False) + '\n')
    temporary.replace(statefile)
    return output


if __name__ == '__main__':
    try:
        result = handle(json.load(sys.stdin))
    except (OSError, ValueError, TypeError, AttributeError):
        result = {'systemMessage': 'Предупреждение о контексте не удалось проверить; процент и сохранение заметки не подтверждены'}
    print(json.dumps(result, ensure_ascii=False))
