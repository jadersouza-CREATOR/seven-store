import os, json, base64, uuid
from decimal import Decimal
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from flask import request, session, redirect, jsonify, send_file
from io import BytesIO


def instalar(mod):
    app = mod.app
    if getattr(app, '_pagamento_v06_ok', False):
        return
    app._pagamento_v06_ok = True

    banco = mod.conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS pagamento_id VARCHAR(100)")
            cursor.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS pagamento_status VARCHAR(40)")
            cursor.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS pix_qr_code TEXT")
            cursor.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS pix_copia_cola TEXT")
            cursor.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS pago_em TIMESTAMP")
        banco.commit()
    finally:
        banco.close()

    def token():
        return os.environ.get('MERCADOPAGO_ACCESS_TOKEN', '').strip()

    def base_url():
        return os.environ.get('PUBLIC_BASE_URL', 'https://seven-store.onrender.com').rstrip('/')

    def consultar_pagamento(payment_id):
        access = token()
        if not access:
            raise RuntimeError('MERCADOPAGO_ACCESS_TOKEN não configurado no Render.')
        req = Request(f'https://api.mercadopago.com/v1/payments/{payment_id}', headers={'Authorization': 'Bearer ' + access, 'Content-Type': 'application/json'}, method='GET')
        with urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode('utf-8'))

    def criar_pagamento(venda_id, total, descricao):
        access = token()
        if not access:
            raise RuntimeError('Configure MERCADOPAGO_ACCESS_TOKEN no Render antes de receber Pix.')
        email = os.environ.get('MERCADOPAGO_PAYER_EMAIL', '').strip()
        if not email:
            raise RuntimeError('Configure MERCADOPAGO_PAYER_EMAIL no Render.')
        body = {
            'transaction_amount': float(Decimal(total)),
            'description': (descricao or f'Venda Seven Store #{venda_id}')[:250],
            'payment_method_id': 'pix',
            'payer': {'email': email},
            'external_reference': str(venda_id),
            'notification_url': base_url() + '/webhook/pix'
        }
        req = Request('https://api.mercadopago.com/v1/payments', data=json.dumps(body).encode('utf-8'), headers={'Authorization': 'Bearer ' + access, 'Content-Type': 'application/json', 'X-Idempotency-Key': str(uuid.uuid4())}, method='POST')
        try:
            with urlopen(req, timeout=25) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as e:
            detalhe = e.read().decode('utf-8', errors='replace')
            raise RuntimeError('Mercado Pago recusou a cobrança: ' + detalhe[:500])

    def criar_cobranca(total, descricao):
        uid = mod.usuario_id_atual()
        caixa = mod.garantir_caixa(uid)
        banco = mod.conectar_banco()
        try:
            with banco.cursor() as cursor:
                cursor.execute("SELECT id FROM vendas WHERE usuario_id=%s AND status='pendente' AND pagamento='pix' AND total=%s AND pagamento_id IS NOT NULL ORDER BY id DESC LIMIT 1", (uid, total))
                existente = cursor.fetchone()
                if existente:
                    venda_id = existente['id']
                    cursor.execute("SELECT pagamento_id,pix_qr_code,pix_copia_cola,pagamento_status FROM vendas WHERE id=%s", (venda_id,))
                    return venda_id, cursor.fetchone()
                cursor.execute("INSERT INTO vendas (usuario_id,caixa_id,total,pagamento,descricao,status,pagamento_status) VALUES (%s,%s,%s,'pix',%s,'pendente','pending') RETURNING id", (uid, caixa['id'], total, descricao))
                venda_id = cursor.fetchone()['id']
            banco.commit()
        finally:
            banco.close()
        pagamento = criar_pagamento(venda_id, total, descricao)
        data = pagamento.get('point_of_interaction', {}).get('transaction_data', {})
        qr = data.get('qr_code') or ''
        qr64 = data.get('qr_code_base64') or ''
        banco = mod.conectar_banco()
        try:
            with banco.cursor() as cursor:
                cursor.execute("UPDATE vendas SET pagamento_id=%s,pagamento_status=%s,pix_qr_code=%s,pix_copia_cola=%s,status='pendente' WHERE id=%s", (str(pagamento.get('id')), pagamento.get('status', 'pending'), qr64, qr, venda_id))
            banco.commit()
        finally:
            banco.close()
        return venda_id, {'pagamento_id': str(pagamento.get('id')), 'pix_qr_code': qr64, 'pix_copia_cola': qr, 'pagamento_status': pagamento.get('status', 'pending')}

    def criar_pix():
        if not mod.exigir_login():
            return jsonify({'erro': 'Faça login.'}), 401
        dados = request.get_json(silent=True) or {}
        try:
            total = Decimal(str(dados.get('total', '0')).replace(',', '.'))
        except Exception:
            total = Decimal('0')
        descricao = str(dados.get('descricao', '') or '')
        if total <= 0:
            return jsonify({'erro': 'Informe um valor válido.'}), 400
        try:
            venda_id, p = criar_cobranca(total, descricao)
        except Exception as e:
            return jsonify({'erro': str(e)}), 400
        session['pix_venda_id'] = venda_id
        return jsonify({'venda_id': venda_id, 'payment_id': p.get('pagamento_id'), 'qr_code': p.get('pix_copia_cola'), 'qr_code_base64': p.get('pix_qr_code'), 'status': p.get('pagamento_status', 'pending')})

    def status_pix(venda_id):
        if not mod.exigir_login():
            return jsonify({'erro': 'Faça login.'}), 401
        banco = mod.conectar_banco()
        try:
            with banco.cursor() as cursor:
                cursor.execute("SELECT * FROM vendas WHERE id=%s AND usuario_id=%s", (venda_id, mod.usuario_id_atual()))
                venda = cursor.fetchone()
        finally:
            banco.close()
        if not venda:
            return jsonify({'erro': 'Venda não encontrada.'}), 404
        if venda['status'] == 'concluida':
            return jsonify({'status': 'approved', 'venda_id': venda_id})
        if not venda['pagamento_id']:
            return jsonify({'status': 'pending', 'venda_id': venda_id})
        try:
            pagamento = consultar_pagamento(venda['pagamento_id'])
        except Exception as e:
            return jsonify({'erro': str(e)}), 400
        aplicar_pagamento(venda_id, pagamento)
        return jsonify({'status': pagamento.get('status'), 'venda_id': venda_id})

    def aplicar_pagamento(venda_id, pagamento):
        status = pagamento.get('status', '')
        banco = mod.conectar_banco()
        try:
            with banco.cursor() as cursor:
                cursor.execute("SELECT id,total,status,pagamento_id FROM vendas WHERE id=%s FOR UPDATE", (venda_id,))
                venda = cursor.fetchone()
                if not venda or venda['status'] == 'cancelada':
                    return
                valor_pago = Decimal(str(pagamento.get('transaction_amount', '0')))
                valor_venda = Decimal(str(venda['total']))
                aprovado = status == 'approved' and valor_pago == valor_venda and str(pagamento.get('id')) == str(venda['pagamento_id'])
                if aprovado:
                    cursor.execute("UPDATE vendas SET status='concluida',pagamento_status='approved',pago_em=CURRENT_TIMESTAMP WHERE id=%s", (venda_id,))
                else:
                    cursor.execute("UPDATE vendas SET pagamento_status=%s WHERE id=%s", (status or 'pending', venda_id))
            banco.commit()
        finally:
            banco.close()

    def webhook_pix():
        data = request.get_json(silent=True) or {}
        payment_id = data.get('data', {}).get('id') or request.args.get('data.id') or request.args.get('id')
        if not payment_id:
            return 'EVENT_RECEIVED', 200
        try:
            pagamento = consultar_pagamento(str(payment_id))
            external = pagamento.get('external_reference')
            if external:
                aplicar_pagamento(int(external), pagamento)
        except Exception:
            return 'EVENT_RECEIVED', 200
        return 'EVENT_RECEIVED', 200

    def pix_qr():
        if not mod.exigir_login():
            return redirect('/')
        venda_id = session.get('pix_venda_id')
        if not venda_id:
            return 'Nenhuma cobrança Pix ativa.', 400
        banco = mod.conectar_banco()
        try:
            with banco.cursor() as cursor:
                cursor.execute("SELECT pix_qr_code FROM vendas WHERE id=%s AND usuario_id=%s AND status='pendente'", (venda_id, mod.usuario_id_atual()))
                venda = cursor.fetchone()
        finally:
            banco.close()
        if not venda or not venda['pix_qr_code']:
            return 'QR Code de pagamento não disponível.', 404
        try:
            imagem = base64.b64decode(venda['pix_qr_code'])
        except Exception:
            return 'QR Code inválido.', 500
        return send_file(BytesIO(imagem), mimetype='image/png')

    original_vendas = app.view_functions.get('vendas')

    def vendas_seguras():
        if request.method == 'POST' and request.form.get('pagamento', '').strip().lower() == 'pix':
            venda_id = session.get('pix_venda_id')
            if not venda_id:
                return redirect('/vendas?pix_erro=Gere o QR Code Pix antes de confirmar o pagamento.')
            banco = mod.conectar_banco()
            try:
                with banco.cursor() as cursor:
                    cursor.execute("SELECT pagamento_id,total,status FROM vendas WHERE id=%s AND usuario_id=%s", (venda_id, mod.usuario_id_atual()))
                    venda = cursor.fetchone()
            finally:
                banco.close()
            if not venda:
                return redirect('/vendas?pix_erro=Cobrança Pix não encontrada.')
            try:
                pagamento = consultar_pagamento(venda['pagamento_id'])
                aplicar_pagamento(venda_id, pagamento)
                if pagamento.get('status') == 'approved' and Decimal(str(pagamento.get('transaction_amount', '0'))) == Decimal(str(venda['total'])):
                    session.pop('pix_venda_id', None)
                    return redirect('/vendas?pix_ok=Pagamento confirmado. Venda finalizada.')
                return redirect('/vendas?pix_erro=Pagamento ainda não confirmado. A venda continua pendente.')
            except Exception as e:
                return redirect('/vendas?pix_erro=' + str(e).replace(' ', '+')[:300])
        return original_vendas()

    app.view_functions['vendas'] = vendas_seguras
    app.view_functions['pix_qr'] = pix_qr
    app.add_url_rule('/api/pix/criar', 'criar_pix', criar_pix, methods=['POST'])
    app.add_url_rule('/api/pix/status/<int:venda_id>', 'status_pix', status_pix, methods=['GET'])
    app.add_url_rule('/webhook/pix', 'webhook_pix', webhook_pix, methods=['POST','GET'])

    @app.after_request
    def injetar_controle_pix(response):
        if request.path != '/vendas' or 'text/html' not in response.content_type:
            return response
        html = response.get_data(as_text=True)
        script = """
<script>
(function(){
 const totalEl=document.getElementById('total'),pagEl=document.getElementById('pagamento'),qrEl=document.getElementById('qr'),copyEl=document.getElementById('pixCopia'),statusEl=document.getElementById('statusPix'),form=document.getElementById('vendaForm');
 if(!totalEl||!pagEl||!form)return;
 let vendaPixId=null;
 function valor(){let v=(totalEl.value||'').trim().replace(/R\\$/g,'').replace(/\\s/g,'');if(v.includes(','))v=v.replace(/\\./g,'').replace(',','.');let n=Number(v);return Number.isFinite(n)&&n>0?n:0}
 window.gerarQR=async function(){
   if(pagEl.value!=='pix'){statusEl.textContent='Selecione Pix como forma de pagamento.';return}
   const v=valor(); if(!v){statusEl.textContent='Informe um valor válido.';return}
   statusEl.textContent='Criando cobrança Pix segura...';
   try{
     const r=await fetch('/api/pix/criar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({total:v.toFixed(2),descricao:form.querySelector('[name=descricao]').value||''})});
     const d=await r.json(); if(!r.ok)throw new Error(d.erro||'Não foi possível criar a cobrança.');
     vendaPixId=d.venda_id; if(qrEl)qrEl.src='/pix/qr?x='+Date.now(); if(copyEl)copyEl.value=d.qr_code||''; statusEl.textContent='🟡 Aguardando pagamento. O dinheiro ainda não foi confirmado.';
   }catch(e){statusEl.textContent=e.message}
 };
 window.copiarPix=async function(){
   if(!copyEl||!copyEl.value){statusEl.textContent='Gere o QR Code Pix primeiro.';return}
   try{await navigator.clipboard.writeText(copyEl.value)}catch(e){copyEl.focus();copyEl.select();document.execCommand('copy')}
   statusEl.textContent='Pix Copia e Cola copiado. A venda só será finalizada após confirmação do pagamento.';
 };
 form.addEventListener('submit',function(ev){
   if(pagEl.value!=='pix')return;
   ev.preventDefault();
   if(!vendaPixId){statusEl.textContent='Gere o QR Code Pix antes de confirmar.';return}
   statusEl.textContent='Verificando se o dinheiro caiu na conta...';
   form.submit();
 });
 const qs=new URLSearchParams(location.search);if(qs.get('pix_ok')){alert(qs.get('pix_ok'));history.replaceState({},'',location.pathname)}if(qs.get('pix_erro')){if(statusEl)statusEl.textContent='❌ '+qs.get('pix_erro');history.replaceState({},'',location.pathname)}
})();
</script>
"""
        if '</body>' in html:
            html = html.replace('</body>', script + '</body>')
            response.set_data(html)
        return response