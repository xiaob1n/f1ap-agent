"""Explicit real-Broker integration entry point; never used by default discovery."""
import os

from f1ap_agent.messaging.__main__ import main as consumer_main


def main() -> int:
    """Require the explicit environment and delegate to the production CLI."""
    required = ('F1AP_RABBITMQ_HOST', 'F1AP_RABBITMQ_PORT', 'F1AP_RABBITMQ_VHOST',
                'F1AP_RABBITMQ_USERNAME', 'F1AP_RABBITMQ_PASSWORD')
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        print('BLOCKED missing environment: ' + ','.join(missing))
        return 2
    return consumer_main()


if __name__ == '__main__':
    raise SystemExit(main())
