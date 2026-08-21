def post_worker_init(worker):
    import app as modulo
    from atendimento_extension import instalar
    instalar(modulo)
