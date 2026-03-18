#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

DEFAULT_RUNSERVER_PORT = "8081"


def normalize_runserver_arguments(argv):
    if len(argv) < 2 or argv[1] != "runserver":
        return argv

    normalized_argv = list(argv)
    if any(argument in {"-h", "--help"} for argument in normalized_argv[2:]):
        return normalized_argv

    addrport_index = None
    for index, argument in enumerate(normalized_argv[2:], start=2):
        if not argument.startswith("-"):
            addrport_index = index
            break

    if addrport_index is None:
        normalized_argv.append(f"127.0.0.1:{DEFAULT_RUNSERVER_PORT}")
        return normalized_argv

    addrport = normalized_argv[addrport_index]

    if addrport.isdigit():
        if addrport == "8000":
            normalized_argv[addrport_index] = DEFAULT_RUNSERVER_PORT
        return normalized_argv

    if addrport.startswith("[") and "]:" in addrport:
        host, port = addrport.rsplit(":", 1)
        if port == "8000":
            normalized_argv[addrport_index] = f"{host}:{DEFAULT_RUNSERVER_PORT}"
        return normalized_argv

    if ":" in addrport:
        host, port = addrport.rsplit(":", 1)
        if port == "8000":
            normalized_argv[addrport_index] = f"{host}:{DEFAULT_RUNSERVER_PORT}"
        return normalized_argv

    normalized_argv[addrport_index] = f"{addrport}:{DEFAULT_RUNSERVER_PORT}"
    return normalized_argv


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mahilmart_project.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(normalize_runserver_arguments(sys.argv))


if __name__ == '__main__':
    main()
