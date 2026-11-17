"""Compatibility command: deploy both Pi services through the shared workflow."""
from deploy import main as deploy_main


def main():
    return deploy_main(['apply', '--target', 'all'])


if __name__ == '__main__':
    raise SystemExit(main())
