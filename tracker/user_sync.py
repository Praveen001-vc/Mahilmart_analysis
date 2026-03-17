from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import UserAccountProfile
from .sales_sync import build_sqlserver_connection_string, normalize_text, pyodbc
from .user_roles import apply_user_role, infer_role_from_source


USER_SYNC_SELECT = """
SELECT
    User_SNo AS User_SNo,
    ISNULL(User_Name, '') AS User_Name,
    ISNULL(User_MtName, '') AS User_MtName,
    ISNULL(User_Passwrd, '') AS User_Passwrd,
    ISNULL(User_CPasswrd, '') AS User_CPasswrd
FROM dbo.User_Table
ORDER BY User_SNo
"""

USER_SYNC_SOURCE_REFERENCE = "SQLSERVER:dbo.User_Table"

User = get_user_model()


class UserSyncError(RuntimeError):
    pass


@dataclass
class UserSyncStats:
    fetched_count: int = 0
    inserted_count: int = 0
    refreshed_count: int = 0
    skipped_count: int = 0


def normalize_source_user_no(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def choose_source_password(password, confirm_password):
    primary_password = normalize_text(password)
    if primary_password:
        return primary_password
    return normalize_text(confirm_password)


def get_existing_user_for_sync(source_user_no, username):
    if source_user_no is not None:
        profile = (
            UserAccountProfile.objects.select_related("user")
            .filter(source_user_no=source_user_no)
            .first()
        )
        if profile is not None:
            return profile.user

    if username:
        return User.objects.filter(username__iexact=username).first()
    return None


def sync_users_from_rows(rows):
    stats = UserSyncStats()

    for row in rows:
        stats.fetched_count += 1

        username = normalize_text(getattr(row, "User_Name", ""))
        master_name = normalize_text(getattr(row, "User_MtName", "")) or username
        password = choose_source_password(
            getattr(row, "User_Passwrd", ""),
            getattr(row, "User_CPasswrd", ""),
        )
        source_user_no = normalize_source_user_no(getattr(row, "User_SNo", None))

        if not username or not password:
            stats.skipped_count += 1
            continue

        existing_user = get_existing_user_for_sync(source_user_no, username)
        if existing_user is not None:
            stats.skipped_count += 1
            continue

        user = User(username=username)

        user.username = username
        user.is_active = True
        apply_user_role(
            user,
            infer_role_from_source(
                username=username,
                master_name=master_name,
            ),
        )
        user.set_password(password)

        with transaction.atomic():
            user.save()
            profile, _ = UserAccountProfile.objects.get_or_create(user=user)
            profile.master_name = master_name
            profile.source_reference = USER_SYNC_SOURCE_REFERENCE
            profile.source_user_no = source_user_no
            profile.save()

        stats.inserted_count += 1

    return stats


def sync_users_from_sqlserver(batch_size=500):
    try:
        connection_string = build_sqlserver_connection_string()
    except Exception as exc:
        raise UserSyncError(str(exc)) from exc

    if pyodbc is None:
        raise UserSyncError(
            "pyodbc is not installed. Add pyodbc to the environment before syncing users."
        )

    try:
        connection = pyodbc.connect(
            connection_string,
            timeout=10,
        )
    except Exception as exc:  # pragma: no cover - integration failure path.
        raise UserSyncError(f"Could not connect to SQL Server: {exc}") from exc

    stats = UserSyncStats()
    try:
        cursor = connection.cursor()
        cursor.execute(USER_SYNC_SELECT)

        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break

            batch_stats = sync_users_from_rows(rows)
            stats.fetched_count += batch_stats.fetched_count
            stats.inserted_count += batch_stats.inserted_count
            stats.refreshed_count += batch_stats.refreshed_count
            stats.skipped_count += batch_stats.skipped_count
    except Exception as exc:  # pragma: no cover - integration failure path.
        raise UserSyncError(f"Could not sync users data: {exc}") from exc
    finally:
        connection.close()

    return stats
