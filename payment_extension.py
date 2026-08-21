import os, json, uuid, hashlib, hmac
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from flask import request, redirect, render_template_string

def instalar(app):
    if getattr(app, '_seven_payment_v06', False):
        return
    app._seven_payment_v06 = True
    original_connect = app.view_functions.get('vendas')

    def db():
        return app.config['SEVEN_DB']()

    def init_payment_db():
        conn=db()
        try:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS pagamento_id VARCHAR(100)")
                cur.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS pagamento_status VARCHAR(30)")
                cur.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS pagamento_qr TEXT")
                cur.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS pagamento_copia_cola TEXT")
                cur.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS pagamento_criado_em TIMESTAMP")
                cur.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS pagamento_aprovado_em TIMESTAMP")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_vendas_pagamento_id ON vendas(pagamento_id)")
            conn.commit()
        finally:
            conn.close()

    app.config['SEVEN_DB'] = app.view_functions['pix_qr'].__globals__['conectar_banco']
    init_payment_db()

    def mp_token():
        return os.environ.get('MERCADOPAGO_ACCESS_TOKEN','').strip()

    def mp_request(method, path, payload=None, idem=None):
        token=mp_token()
        if not token:
            raise RuntimeError('MERCADOPAGO_ACCESS_TOKEN não configurado no Render.')
        data=json.dumps(payload).encode() if payload is not None else None
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
        if idem: headers['X-Idempotency-Key']=idem
        req=Request('https://api.mercadopago.com'+path,data=data,headers=headers,method=method)
        try:
            with urlopen(req,timeout=20) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            body=e.read().decode(errors='replace')
            raise RuntimeError('Mercado Pago: '+body[:700])

    def criar_pix(venda_id,total,descricao,email):
        base=os.environ.get('PUBLIC_BASE_URL','https://seven-store.onrender.com').rstrip('/')
        payload={
            'transaction_amount':float(total),
            'description':descricao or ('Seven Store - Venda #'+str(venda_id)),
            'payment_method_id':'pix',
            'notification_url':base+'/webhook/mercadopago',
            'external_reference':str(venda_id),
            'payer':{'email':email}
        }
        p=mp_request('POST','/v1/payments',payload,str(uuid.uuid4()))
        tx=(p.get('point_of_interaction') or {}).get('transaction_data') or {}
        pid=str(p.get('id'))
        status=p.get('status','pending')
        conn=db()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE vendas SET pagamento_id=%s,pagamento_status=%s,pagamento_qr=%s,pagamento_copia_cola=%s,pagamento_criado_em=CURRENT_TIMESTAMP,status='aguardando_pagamento' WHERE id=%s",(pid,status,tx.get('qr_code_base64'),tx.get('qr_code'),venda_id))
            conn.commit()
        finally: conn.close()
        return p

    @app.before_request
    def interceptar_pix_v06():
        if request.endpoint != 'vendas' or request.method != 'POST':
            return None
        if not app.view_functions.get('vendas'):
            return None
        pagamento=request.form.get('pagamento','').strip().lower()
        if pagamento!='pix':
            return None
        try:
            total=Decimal(request.form.get('total','0').replace(',','.'))
            if total<=0: raise InvalidOperation
        except Exception:
            return None
        email=request.form.get('email_cliente','').strip().lower()
        if '@' not in email:
            return render_template_string(EMAIL_PAGE)
        uid=app.view_functions['usuario_id_atual']()
        caixa=app.view_functions['garantir_caixa'](uid)
        descricao=request.form.get('descricao','').strip()
        conn=db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pix_chave FROM configuracoes_loja WHERE id=1")
                loja=cur.fetchone() or {}
                if not loja.get('pix_chave'):
                    return redirect('/vendas?erro=Chave+Pix+nao+cadastrada')
                cur.execute("INSERT INTO vendas(usuario_id,caixa_id,total,pagamento,descricao,pix_chave,status,pagamento_status) VALUES(%s,%s,%s,'pix',%s,%s,'aguardando_pagamento','pending') RETURNING id",(uid,caixa['id'],total,descricao,loja['pix_chave']))
                venda_id=cur.fetchone()['id']
            conn.commit()
        finally: conn.close()
        try:
            p=criar_pix(venda_id,total,descricao,email)
        except Exception as e:
            conn=db()
            try:
                with conn.cursor() as cur: cur.execute("UPDATE vendas SET status='cancelada',motivo_cancelamento=%s,cancelada_em=CURRENT_TIMESTAMP WHERE id=%s",(str(e)[:255],venda_id))
                conn.commit()
            finally: conn.close()
            return render_template_string(ERROR_PAGE,error=str(e))
        return render_template_string(PAYMENT_PAGE,venda_id=venda_id,total=f'{total:.2f}',qr=p.get('point_of_interaction',{}).get('transaction_data',{}).get('qr_code_base64',''),copia=p.get('point_of_interaction',{}).get('transaction_data',{}).get('qr_code',''))

    @app.get('/api/pagamento/<int:venda_id>')
    def status_pagamento_v06(venda_id):
        if not app.view_functions['exigir_login'](): return {'erro':'Não autenticado'},401
        conn=db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id,total,pagamento,status,pagamento_id,pagamento_status FROM vendas WHERE id=%s AND usuario_id=%s",(venda_id,app.view_functions['usuario_id_atual']()))
                v=cur.fetchone()
        finally: conn.close()
        if not v: return {'erro':'Venda não encontrada'},404
        if v['pagamento_id']:
            try:
                p=mp_request('GET','/v1/payments/'+str(v['pagamento_id']))
                st=p.get('status','pending')
                if st=='approved': confirmar(venda_id,v['pagamento_id'])
                elif st in ('rejected','cancelled','refunded','charged_back'): marcar_falha(venda_id,st)
                v['pagamento_status']=st
            except Exception: pass
        return {'id':venda_id,'status':v['pagamento_status'] or 'pending','venda_status':v['status']}

    def confirmar(venda_id,payment_id):
        conn=db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT total,status FROM vendas WHERE id=%s FOR UPDATE",(venda_id,)); v=cur.fetchone()
                if not v or v['status']=='cancelada': return
                cur.execute("SELECT status,transaction_amount,external_reference FROM pagamentos_tmp WHERE payment_id=%s",(payment_id,)) if False else None
                cur.execute("UPDATE vendas SET status='concluida',pagamento_status='approved',pagamento_aprovado_em=CURRENT_TIMESTAMP WHERE id=%s AND status='aguardando_pagamento'",(venda_id,))
            conn.commit()
        finally: conn.close()

    def marcar_falha(venda_id,status):
        conn=db()
        try:
            with conn.cursor() as cur: cur.execute("UPDATE vendas SET pagamento_status=%s WHERE id=%s AND status='aguardando_pagamento'",(status,venda_id))
            conn.commit()
        finally: conn.close()

    @app.post('/webhook/mercadopago')
    def webhook_mercadopago_v06():
        data=request.get_json(silent=True) or {}
        pid=data.get('data',{}).get('id') or request.args.get('id')
        typ=data.get('type') or request.args.get('topic')
        if typ not in ('payment','payments') or not pid:
            return 'ok',200
        try:
            p=mp_request('GET','/v1/payments/'+str(pid))
            ref=p.get('external_reference')
            if ref and p.get('status')=='approved':
                confirmar(int(ref),str(pid))
            elif ref and p.get('status') in ('rejected','cancelled','refunded','charged_back'):
                marcar_falha(int(ref),p.get('status'))
        except Exception:
            return 'ok',200
        return 'ok',200

EMAIL_PAGE='''<!doctype html><meta charset="utf-8"><title>Pix - Seven Store</title><style>body{font-family:Arial;max-width:500px;margin:50px auto;padding:20px}.box{padding:25px;border-radius:15px;box-shadow:0 5px 20px #ddd}input,button{width:100%;padding:13px;margin-top:10px;box-sizing:border-box}button{cursor:pointer}</style><div class="box"><h2>💳 Pagamento Pix</h2><p>Para criar a cobrança Pix, informe o e-mail do cliente.</p><form method="post" action="/vendas"><input type="hidden" name="total" value="{{ request.form.get('total','') }}"><input type="hidden" name="pagamento" value="pix"><input type="hidden" name="descricao" value="{{ request.form.get('descricao','') }}"><input name="email_cliente" type="email" required placeholder="E-mail do cliente"><button>Gerar cobrança Pix</button></form></div>'''
ERROR_PAGE='''<!doctype html><meta charset="utf-8"><style>body{font-family:Arial;max-width:600px;margin:50px auto;padding:20px}.box{padding:25px;border-radius:15px;box-shadow:0 5px 20px #ddd}a{display:inline-block;margin-top:15px}</style><div class="box"><h2>❌ Não foi possível criar o pagamento</h2><p>{{ error }}</p><a href="/vendas">Voltar para vendas</a></div>'''
PAYMENT_PAGE='''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pagamento Pix - Seven Store</title><style>body{font-family:Arial;background:#f4f6f8;margin:0;padding:25px}.box{background:#fff;max-width:480px;margin:auto;padding:25px;border-radius:18px;box-shadow:0 5px 25px #ddd;text-align:center}img{max-width:280px}.copy{width:100%;min-height:90px;box-sizing:border-box;margin-top:15px}.ok{color:#16803c;font-weight:bold}.wait{color:#a16207;font-weight:bold}button,a{display:block;width:100%;box-sizing:border-box;padding:13px;margin-top:12px;border:0;border-radius:9px;text-decoration:none;background:#eee;color:#111;cursor:pointer}</style><div class="box"><h2>💠 Aguardando Pix</h2><h3>Venda #{{ venda_id }} · R$ {{ total }}</h3>{% if qr %}<img src="data:image/png;base64,{{ qr }}" alt="QR Code Pix">{% endif %}<textarea class="copy" readonly>{{ copia }}</textarea><button onclick="navigator.clipboard.writeText(document.querySelector('.copy').value);this.textContent='✅ Copiado'">📋 Copiar Pix</button><p id="status" class="wait">⏳ Aguardando confirmação do pagamento...</p><a href="/vendas">Voltar para vendas</a></div><script>async function checar(){try{const r=await fetch('/api/pagamento/{{ venda_id }}',{cache:'no-store'});const d=await r.json();if(d.status==='approved'){document.getElementById('status').className='ok';document.getElementById('status').textContent='✅ Pagamento confirmado! Venda finalizada.';setTimeout(()=>location.href='/vendas',1200)}else if(['rejected','cancelled','refunded','charged_back'].includes(d.status)){document.getElementById('status').textContent='❌ Pagamento não aprovado: '+d.status}else{document.getElementById('status').textContent='⏳ Aguardando confirmação do pagamento...'}}catch(e){}}checar();setInterval(checar,5000)</script>'''
