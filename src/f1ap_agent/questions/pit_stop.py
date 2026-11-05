"""只用 stop_duration，不用 lane_duration 或 pit_duration。"""
from f1ap_agent.features.ten import Feature
from f1ap_agent.features.cleaning import Insufficient, micros
from f1ap_agent.data.openf1.boundary import timestamp
from f1ap_agent.runtime import Json, json_array, json_integer, json_object, json_string
from f1ap_agent.questions import Distribution, exclusive
from dataclasses import replace


def calculate(feature: Feature, teams: dict[str, tuple[str, ...]]) -> Distribution:
    pools = dict(feature.values)
    winner = min(teams, key=lambda key: (min(v for driver in teams[key] for v in pools[driver]), key))
    return exclusive({key: 10000 if key == winner else 0 for key in teams},
                     'PIT_SERVICE_POINT_V1', 'HISTORICAL_STOP_POINT_PRIOR_V1; stationary seconds only; ties by option key')


def award_prior(source: dict[str, Json], binding: dict[str, Json], keys: tuple[str, ...],
                cutoff: str, target_session: int) -> Distribution:
    """使用完整公开奖项裁定的历史赢家，不把缺失停驻样本当零。"""
    data = json_object(source['data'])
    if (data['event_id'] != binding['event_id'] or binding['session_key'] == target_session
            or timestamp(json_string(binding['available_at'])) > timestamp(cutoff)):
        raise Insufficient('PIT_AWARD_IDENTITY')
    winners = [json_object(row) for row in json_array(data['chart'])
               if json_object(row)['points'] == 25 and json_object(row)['irregular'] is False]
    if len(winners) != 1:
        raise Insufficient('PIT_AWARD_UNRESOLVED')
    winner = winners[0]
    _ = micros(winner['duration'])
    number = json_integer(winner['driverNr'])
    teams = json_object(binding['teams'])
    chosen = [key for key, values in teams.items() if number in [json_integer(v) for v in json_array(values)]]
    if len(chosen) != 1 or set(teams) != set(keys):
        raise Insufficient('PIT_AWARD_CANDIDATE')
    result = exclusive({key: int(key == chosen[0]) for key in keys},
                       'PIT_AWARD_POINT_V1', 'OFFICIAL_HISTORICAL_AWARD_WINNER; not calibrated')
    return replace(result, samples=1, error='Single historical award; transfer error unquantified')
