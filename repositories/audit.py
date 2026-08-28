"""SQL access for the audit log."""


def log(conn, user_id, action, entity, entity_id=None, detail=None):
    conn.execute(
        """INSERT INTO audit_log (user_id, action, entity, entity_id, detail)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, action, entity, entity_id, detail),
    )


def recent(conn, limit=100):
    return conn.execute(
        """SELECT a.*, u.username FROM audit_log a
           LEFT JOIN users u ON u.id = a.user_id
           ORDER BY a.at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
