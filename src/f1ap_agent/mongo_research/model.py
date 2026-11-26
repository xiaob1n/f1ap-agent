"""逐次保存调用意图与使用量；传输失败不重试。"""
import json
from dataclasses import dataclass
from pathlib import Path

import anyio
import httpx

from f1ap_agent.runtime import (
    Json,
    RuntimeFault,
    atomic_json,
    json_array,
    json_integer,
    json_object,
)

from .config import Config
from .query import TOOL
from .skills import TOOL as SKILL_TOOL


@dataclass(frozen=True, slots=True)
class Model:
    config: Config
    output: Path
    key: str

    async def call(self, client: httpx.AsyncClient, messages: list[dict[str, Json]], round_no: int) -> dict[str, Json]:
        path = self.output / f'model-{round_no:03d}.json'
        receipt: dict[str, Json] = {'round': round_no, 'outcome': 'UNKNOWN',
            'input_tokens': None, 'output_tokens': None, 'cost': None, 'cost_status': 'UNKNOWN'}
        body = {'model': 'deepseek-flash', 'stream': False,
                'thinking': {'type': 'disabled'}, 'max_tokens': self.config.max_tokens,
                'response_format': {'type': 'json_object'},
                'messages': messages, 'tools': [TOOL, SKILL_TOOL],
                'tool_choice': 'none' if round_no == self.config.max_model_rounds else 'auto',
                'parallel_tool_calls': False}
        receipt['request'] = body
        atomic_json(path, receipt)
        try:
            with anyio.fail_after(self.config.timeout_seconds):
                response = await client.post('https://api.deepseek.com/chat/completions',
                    headers={'Authorization': 'Bearer ' + self.key}, json=body)
            if response.status_code != 200:
                raise RuntimeFault('LLM_HTTP_' + str(response.status_code))
            raw: Json = json.loads(response.content)
            result = json_object(raw)
            usage = json_object(result['usage'])
            for source, target in [('prompt_tokens', 'input_tokens'), ('completion_tokens', 'output_tokens')]:
                count = json_integer(usage[source])
                if count < 0:
                    raise RuntimeFault('LLM_USAGE_INVALID')
                receipt[target] = count
            choices = json_array(result['choices'])
            if len(choices) != 1:
                raise RuntimeFault('LLM_CHOICES_INVALID')
            choice = json_object(choices[0])
            if choice['finish_reason'] not in ('stop', 'tool_calls'):
                raise RuntimeFault('LLM_OUTPUT_INCOMPLETE')
            message = json_object(choice['message'])
            if message.get('role') != 'assistant':
                raise RuntimeFault('LLM_ROLE_INVALID')
            if round_no == self.config.max_model_rounds and (
                    choice['finish_reason'] == 'tool_calls' or message.get('tool_calls')):
                raise RuntimeFault('LLM_FINAL_TOOL_CALL')
            receipt['outcome'] = 'SUCCEEDED'
            receipt['response'] = message
            return message
        except (httpx.TransportError, TimeoutError):
            receipt['reason'] = 'LLM_TRANSPORT_UNKNOWN'
            raise RuntimeFault('LLM_TRANSPORT_UNKNOWN') from None
        except (ValueError, KeyError, RuntimeFault) as error:
            receipt['reason'] = error.code if isinstance(error, RuntimeFault) else 'LLM_RESPONSE_INVALID'
            raise RuntimeFault(str(receipt['reason'])) from None
        finally:
            atomic_json(path, receipt)
