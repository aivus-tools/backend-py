from django.db import connections


def post_fork(server, worker):
    connections.close_all()
