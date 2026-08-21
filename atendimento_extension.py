from flask import render_template, request, redirect, url_for

def instalar(mod):
    app = mod.app
    if getattr(app, '_atendimento_v06_ok', False): return
    app._atendimento_v06_ok = True

    def atendimento():
        if not mod.exigir_login():
            return redirect('/')
        conexao = mod.conectar_banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SELECT id,canal,identificador,nome,status,ultima_mensagem FROM conversas ORDER BY id DESC")
                conversas = cursor.fetchall()
                conversa_id = request.args.get('conversa', type=int)
                atual = None
                mensagens = []
                if conversa_id:
                    cursor.execute("SELECT * FROM conversas WHERE id=%s", (conversa_id,))
                    atual = cursor.fetchone()
                    if atual:
                        cursor.execute("SELECT * FROM mensagens WHERE conversa_id=%s ORDER BY criada_em", (conversa_id,))
                        mensagens = cursor.fetchall()
                cursor.execute("SELECT canal,ativo FROM integracoes ORDER BY canal")
                integracoes = cursor.fetchall()
        finally:
            conexao.close()
        estados = {'whatsapp': 'Offline', 'instagram': 'Offline'}
        for item in integracoes:
            if item['canal'] in estados and item['ativo']:
                estados[item['canal']] = 'Conectado'
        return render_template('atendimento.html', **mod.contexto_base(),
                               conversas=conversas, conversa_atual=atual,
                               mensagens=mensagens, conversa_selecionada=conversa_id,
                               whatsapp_status=estados['whatsapp'],
                               instagram_status=estados['instagram'])

    def enviar(conversa_id):
        if not mod.exigir_login():
            return redirect('/')
        texto = request.form.get('mensagem', '').strip()
        if not texto:
            return redirect(url_for('atendimento', conversa=conversa_id))
        conexao = mod.conectar_banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SELECT * FROM conversas WHERE id=%s", (conversa_id,))
                conv = cursor.fetchone()
                if not conv:
                    return redirect('/atendimento')
                cursor.execute("INSERT INTO mensagens(conversa_id,direcao,texto) VALUES(%s,'enviada',%s)", (conversa_id, texto))
                cursor.execute("UPDATE conversas SET ultima_mensagem=%s WHERE id=%s", (texto, conversa_id))
            conexao.commit()
        finally:
            conexao.close()
        return redirect(url_for('atendimento', conversa=conversa_id))

    app.view_functions['atendimento'] = atendimento
    if 'enviar_mensagem' not in app.view_functions:
        app.add_url_rule('/atendimento/mensagem/<int:conversa_id>', 'enviar_mensagem', enviar, methods=['POST'])
