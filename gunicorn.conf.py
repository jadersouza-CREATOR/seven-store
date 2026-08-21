def post_worker_init(worker):
    import app as modulo
    from atendimento_extension import instalar as instalar_atendimento
    instalar_atendimento(modulo)
    from pagamento_extension import instalar as instalar_pagamento
    instalar_pagamento(modulo)
