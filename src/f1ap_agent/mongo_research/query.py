"""将模型筛选条件收敛为固定库、固定会话的只读 find。"""
import json
from dataclasses import dataclass
from typing import Final

import anyio
from mcp import ClientSession

from f1ap_agent.runtime import (
    Json,
    RuntimeFault,
    json_array,
    json_integer,
    json_object,
    json_string,
)

from .config import Config, Request

FIELDS: Final = {
    'sessions': 'session_key session_name session_type meeting_key date_start date_end circuit_short_name country_name year',
    'drivers': 'session_key driver_number broadcast_name full_name name_acronym team_name country_code',
    'laps': 'session_key driver_number lap_number date_start lap_duration duration_sector_1 duration_sector_2 duration_sector_3 is_pit_out_lap',
    'pit': 'session_key driver_number lap_number date pit_duration lane_duration stop_duration',
    'weather': 'session_key date air_temperature track_temperature humidity pressure rainfall wind_direction wind_speed',
    'race_control': 'session_key date driver_number lap_number category flag message scope sector',
    'stints': 'session_key driver_number stint_number lap_start compound tyre_age_at_start',
}
DATES: Final = {'sessions': 'date_start', 'laps': 'date_start', 'pit': 'date',
                'weather': 'date', 'race_control': 'date', 'stints': '_date_start_last_lap'}
TOOL: Final = {'type': 'function', 'function': {
    'name': 'find',
    'description': '查询当前观察到的 F1 数据；固定会话和截止时间，非历史快照。返回有界样本，不能假定完整。stints 不提供可变 lap_end。',
    'parameters': {'type': 'object', 'additionalProperties': False,
        'properties': {'collection': {'type': 'string', 'enum': list(FIELDS)},
                       'driver_number': {'type': 'integer', 'minimum': 1},
                       'lap_number': {'type': 'integer', 'minimum': 1},
                       'limit': {'type': 'integer', 'minimum': 1}},
        'required': ['collection']}}}


@dataclass(frozen=True, slots=True)
class Query:
    request: Request
    config: Config

    def arguments(self, raw: Json) -> dict[str, Json]:
        value = json_object(raw)
        if set(value) - {'collection', 'driver_number', 'lap_number', 'limit'}:
            raise RuntimeFault('QUERY_FIELDS')
        collection = json_string(value['collection'])
        if collection not in FIELDS:
            raise RuntimeFault('QUERY_COLLECTION')
        limit = json_integer(value.get('limit', self.config.row_limit))
        if not 1 <= limit <= self.config.row_limit:
            raise RuntimeFault('QUERY_LIMIT')
        filters: dict[str, Json] = {
            '_id' if collection == 'sessions' else 'session_key': self.request.session_key}
        for field in ('driver_number', 'lap_number'):
            if field in value:
                number = json_integer(value[field])
                if field not in FIELDS[collection].split() or number <= 0:
                    raise RuntimeFault('QUERY_FILTER')
                filters[field] = number
        if collection in DATES:
            filters[DATES[collection]] = {'$type': 'date', '$lte': {'$date': self.request.cutoff}}
        projection: dict[str, Json] = {field: 1 for field in FIELDS[collection].split()}
        projection['_id'] = 0
        return {'connectionId': 'preconfigured', 'database': 'openf1-livetiming', 'collection': collection,
                'filter': filters, 'projection': projection, 'limit': limit,
                'sort': {DATES.get(collection, 'driver_number'): -1},
                'responseBytesLimit': self.config.byte_limit}

    async def execute(self, session: ClientSession, arguments: dict[str, Json]) -> dict[str, Json]:
        with anyio.fail_after(self.config.timeout_seconds):
            response = await session.call_tool('find', arguments)
        if response.isError:
            raise RuntimeFault('MCP_FIND_FAILED')
        result = json_object(response.structuredContent)
        rows = json_array(result['documents'])
        if len(rows) > json_integer(arguments['limit']):
            raise RuntimeFault('MCP_ROW_LIMIT')
        collection = json_string(arguments['collection'])
        allowed = FIELDS[collection].split()
        documents: list[Json] = [{key: item for key, item in json_object(row).items()
                                 if key in allowed} for row in rows]
        if len(json.dumps(documents, ensure_ascii=False).encode()) > self.config.byte_limit:
            raise RuntimeFault('MCP_BYTE_LIMIT')
        return {'documents': documents, 'applied_limits': result['appliedLimits'],
                'sample_only': True, 'historical_availability_guaranteed': False}
