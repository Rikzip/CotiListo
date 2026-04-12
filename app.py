import streamlit as st
import io
import urllib.parse
import os
import base64
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
import urllib.request
from supabase import create_client, Client, ClientOptions
from PIL import Image  # <-- IMPORT CRUCIAL POUR LA COMPRESSION

# ==========================================
# 📱 GLOBAL CONFIGURATION
# ==========================================
st.set_page_config(
    page_title="CotiListo - Cotizaciones", 
    page_icon="⚡", 
    layout="centered",
    initial_sidebar_state="auto"
)

# --- GLOBAL CSS (Clean UI & Spanish Translation - STABLE VERSION) ---
st.markdown("""
    <style>
    /* General Clean UI */
    .block-container { 
        padding-top: 3rem !important; 
        padding-bottom: 1rem !important; 
    }
    [data-testid="stDecoration"] { display: none !important; }
    footer { display: none !important; }

    /* 🎯 SURGICAL FIX: Hide the right-side header elements (Deploy & 3 dots) without touching the left menu */
    .stDeployButton { display: none !important; }
    [data-testid="stAppDeployButton"] { display: none !important; }
    [data-testid="stMainMenu"] { display: none !important; visibility: hidden !important; }
    [data-testid="stHeaderActionElements"] { display: none !important; }

    /* Hide native browser password reveal icon */
    input::-ms-reveal,
    input::-ms-clear {
        display: none !important;
    }
    
    /* Translate Streamlit file uploader to Spanish */
    [data-testid="stFileUploadDropzone"] div div::before {
        content: "Arrastra y suelta tu logo aquí";
        color: #555;
        display: block;
        margin-bottom: 5px;
    }
    [data-testid="stFileUploadDropzone"] div div span,
    [data-testid="stFileUploadDropzone"] small {
        display: none !important;
    }
    [data-testid="stFileUploadDropzone"] button::before {
        content: "Buscar archivo";
        display: block;
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        width: 100%;
    }
    [data-testid="stFileUploadDropzone"] button span {
        visibility: hidden;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 🔑 SUPABASE CONNECTION
# ==========================================
if 'supabase_client' not in st.session_state:
    url = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY", "")
    if url and key:
        # Force PKCE flow to ensure Magic Links use ?code= instead of #access_token=
        opts = ClientOptions(flow_type="pkce")
        st.session_state.supabase_client = create_client(url, key, options=opts)
    else:
        st.session_state.supabase_client = None

supabase = st.session_state.supabase_client

# --- MAGIC LINK RECOVERY INTERCEPTION ---
if supabase and "code" in st.query_params:
    try:
        # Exchange URL code for an official session
        res = supabase.auth.exchange_code_for_session({"auth_code": st.query_params["code"]})
        st.session_state.user = res.user
        # Clean URL to remove the security code
        st.query_params.clear()
        st.session_state.show_welcome = True
    except Exception as e:
        st.error("El enlace de recuperación es inválido o ha expirado.")
        st.query_params.clear()

# ==========================================
# 🧠 SESSION STATE & PERFORMANCE CACHE
# ==========================================
if 'cart' not in st.session_state:
    st.session_state.cart = []
if 'pdf_ready' not in st.session_state:
    st.session_state.pdf_ready = False
    st.session_state.pdf_bytes = None
    st.session_state.wa_url = ""
if 'user' not in st.session_state:
    st.session_state.user = None
if 'user_profile' not in st.session_state:
    st.session_state.user_profile = {}
if 'clients' not in st.session_state:
    st.session_state.clients = []
if 'show_welcome' not in st.session_state:
    st.session_state.show_welcome = False

@st.cache_data
def get_base64_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()

def fetch_user_data(force=False):
    if st.session_state.user:
        if force or not st.session_state.user_profile:
            try:
                res_prof = supabase.table("profiles").select("*").eq("id", st.session_state.user.id).execute()
                if res_prof.data:
                    st.session_state.user_profile = res_prof.data[0]
                
                res_cli = supabase.table("clients").select("*").eq("user_id", st.session_state.user.id).order("name").execute()
                st.session_state.clients = res_cli.data if res_cli.data else []
            except Exception as e:
                print(f"Error fetching data: {e}")

# ==========================================
# ⚙️ IMAGE PROCESSING HELPER
# ==========================================
def process_image_for_pdf(image_input_stream):
    try:
        img = Image.open(image_input_stream)
        if img.mode in ('RGBA', 'LA'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
            
        target_width = 500
        if img.width > target_width:
            w_percent = (target_width / float(img.width))
            h_size = int((float(img.height) * float(w_percent)))
            img = img.resize((target_width, h_size), Image.Resampling.LANCZOS)
            
        compressed_stream = io.BytesIO()
        img.save(compressed_stream, format='JPEG', quality=75, optimize=True)
        compressed_stream.seek(0)
        
        return compressed_stream
    except Exception as e:
        print(f"Error processing image: {e}")
        image_input_stream.seek(0)
        return image_input_stream

# ==========================================
# ⚙️ PDF GENERATION ENGINE
# ==========================================
def generate_pdf(quote_data):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    brand_blue = (0.09, 0.44, 0.76)
    
    c.setFillColorRGB(brand_blue[0], brand_blue[1], brand_blue[2])
    c.rect(0, height - 0.15 * inch, width, 0.15 * inch, fill=True, stroke=False)
    c.setFillColorRGB(0, 0, 0)
    
    c.setFont("Helvetica-Bold", 22)
    c.drawString(1 * inch, height - 1.0 * inch, "Cotización")
    c.setFont("Helvetica", 10)
    c.drawString(1 * inch, height - 1.25 * inch, f"Fecha: {quote_data['date']}")
    
    logo_drawn = False
    logo_w, logo_h = 2.2 * inch, 1.2 * inch 
    logo_x = width - 1 * inch - logo_w
    logo_y = height - 1.6 * inch
    
    image_stream_to_use = None
    
    if quote_data.get('logo_file'):
        image_stream_to_use = process_image_for_pdf(quote_data['logo_file'])
    elif quote_data.get('logo_url'):
        try:
            req = urllib.request.Request(quote_data['logo_url'], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                remote_img_stream = io.BytesIO(response.read())
                image_stream_to_use = process_image_for_pdf(remote_img_stream)
        except: pass

    if image_stream_to_use:
        try:
            logo_img = ImageReader(image_stream_to_use)
            c.drawImage(logo_img, logo_x, logo_y, width=logo_w, height=logo_h, preserveAspectRatio=True, mask='auto')
            logo_drawn = True
        except: pass

    if not logo_drawn and quote_data.get('seller_name'):
        c.setFont("Helvetica-Bold", 16)
        tw = c.stringWidth(quote_data['seller_name'], "Helvetica-Bold", 16)
        c.drawString(width - 1 * inch - tw, height - 1.1 * inch, quote_data['seller_name'])
    
    y_pos = height - 1.9 * inch
    c.setFont("Helvetica", 12)
    c.drawString(1 * inch, y_pos, f"Cliente: {quote_data['client_name']}")
    y_pos -= 0.2 * inch
    
    c.setFont("Helvetica", 10)
    if quote_data.get('display_phone'):
        c.drawString(1 * inch, y_pos, f"Tel / WhatsApp: {quote_data['display_phone']}")
        y_pos -= 0.2 * inch
    if quote_data.get('client_nit'):
        c.drawString(1 * inch, y_pos, f"NIT / ID Fiscal: {quote_data['client_nit']}")
        y_pos -= 0.2 * inch
        
    if quote_data.get('vehicle_desc') or quote_data.get('vehicle_plate'):
        y_pos -= 0.1 * inch
        c.setFont("Helvetica-Bold", 10)
        c.drawString(1 * inch, y_pos, "Datos del Vehículo:")
        c.setFont("Helvetica", 10)
        y_pos -= 0.2 * inch
        if quote_data.get('vehicle_desc'):
            c.drawString(1 * inch, y_pos, f"Modelo: {quote_data['vehicle_desc']}")
            y_pos -= 0.2 * inch
        if quote_data.get('vehicle_plate'):
            c.drawString(1 * inch, y_pos, f"Placas: {quote_data['vehicle_plate']}")
            y_pos -= 0.2 * inch

    y_pos -= 0.4 * inch
    c.setFillColorRGB(0.93, 0.93, 0.93)
    c.rect(1 * inch, y_pos - 0.08 * inch, 6.5 * inch, 0.25 * inch, fill=True, stroke=False)
    
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(1.1 * inch, y_pos, "Descripción")
    c.drawString(4.5 * inch, y_pos, "Cant.")
    c.drawString(5.5 * inch, y_pos, f"Total ({quote_data['currency']})")
    
    y_pos -= 0.3 * inch
    c.setFont("Helvetica", 10)
    
    for item in quote_data['cart']:
        c.drawString(1.1 * inch, y_pos, item['desc'][:45])
        c.drawString(4.5 * inch, y_pos, str(item['qty']))
        c.drawString(5.5 * inch, y_pos, f"{item['total']:.2f}")
        y_pos -= 0.25 * inch

    y_pos -= 0.1 * inch
    c.line(4 * inch, y_pos, 7.5 * inch, y_pos) 
    y_pos -= 0.25 * inch 
    
    c.setFont("Helvetica-Bold", 11)
    c.drawString(4.2 * inch, y_pos, "Total:")
    c.drawString(5.5 * inch, y_pos, f"{quote_data['currency']} {quote_data['grand_total']:.2f}")
    
    if quote_data.get('advance_amount', 0) > 0:
        y_pos -= 0.25 * inch
        c.setFont("Helvetica", 10)
        c.drawString(4.2 * inch, y_pos, "Anticipo Requerido:")
        c.drawString(5.5 * inch, y_pos, f"{quote_data['currency']} {quote_data['advance_amount']:.2f}")
        y_pos -= 0.2 * inch
        c.setFont("Helvetica-Bold", 10)
        c.drawString(4.2 * inch, y_pos, "Saldo a Pagar:")
        c.drawString(5.5 * inch, y_pos, f"{quote_data['currency']} {quote_data['balance_due']:.2f}")

    y_pos = 1.8 * inch
    c.setStrokeColorRGB(0.8, 0.8, 0.8)
    c.line(1 * inch, y_pos, 7.5 * inch, y_pos)
    c.setStrokeColorRGB(0, 0, 0)
    y_pos -= 0.2 * inch
    
    has_bank_details = quote_data.get('bank_name') and quote_data.get('account_number')
    if has_bank_details:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(1 * inch, y_pos, "Información de Pago:")
        c.setFont("Helvetica", 9)
        bank_str = f"{quote_data['bank_name']} - Cuenta {quote_data.get('account_type', 'Monetaria')} No. {quote_data['account_number']}"
        if quote_data.get('account_name'):
            bank_str += f" (A nombre de: {quote_data['account_name']})"
        c.drawString(2.6 * inch, y_pos, bank_str)
        y_pos -= 0.2 * inch
        
    if quote_data.get('terms'):
        c.setFont("Helvetica-Bold", 9)
        c.drawString(1 * inch, y_pos, "Condiciones:")
        c.setFont("Helvetica", 9)
        c.drawString(2.6 * inch, y_pos, quote_data['terms'][:80])

    c.setFont("Helvetica-Oblique", 9)
    c.setFillColorRGB(0.5, 0.5, 0.5) 
    promo_text = "Generado con CotiListo.com"
    tw = c.stringWidth(promo_text, "Helvetica-Oblique", 9)
    c.drawString((width / 2) - (tw / 2), 0.5 * inch, promo_text)
    
    c.showPage()
    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

# ==========================================
# 📂 PAGE: GENERADOR
# ==========================================
def page_free_generator():
    if st.session_state.get('show_welcome'):
        st.toast("¡Conexión exitosa!", icon="👋")
        st.session_state.show_welcome = False 

    fetch_user_data()
    profile = st.session_state.user_profile

    if os.path.exists("logo_cotilisto.png"):
        img_b64 = get_base64_image("logo_cotilisto.png")
        st.markdown(f'<div style="text-align: center; margin-top: 0px; padding-bottom: 5px;"><img src="data:image/png;base64,{img_b64}" width="160"></div>', unsafe_allow_html=True)
    else:
        st.markdown("<h1 style='text-align: center; margin-top: 0px;'>CotiListo</h1>", unsafe_allow_html=True)
    
    st.markdown("<h3 style='text-align: center; color: #555; margin-top: 0px; margin-bottom: 20px;'>Crea tu cotización en segundos</h3>", unsafe_allow_html=True)

    if not st.session_state.user:
        with st.container(border=True):
            st.markdown("#### ⚡ Trabaja más rápido")
            st.write("Guarda tu logo, arma tu catálogo de precios y guarda tu historial. **¡Únete gratis!** 🚀")
            st.page_link(page_log, label="Crear cuenta gratis / Entrar", icon="✨")

    default_currency = profile.get("currency", "Q")
    if st.session_state.user:
        col1, col2 = st.columns([1, 2])
        currency = col1.radio("Moneda:", ["Q", "$"], index=0 if default_currency=="Q" else 1, horizontal=True)
        col2.info(f"✨ Modo personalizado: **{profile.get('business_name', 'tu negocio')}**")
        template = "Personalizado"
    else:
        col1, col2 = st.columns(2)
        currency = col1.radio("Moneda:", ["Q", "$"], horizontal=True)
        template = col2.selectbox("Tipo de negocio:", ["General", "Taller Mecánico / Motos", "Odontología / Dentista", "Clínica Médica", "Construcción / Carpintería", "Freelance / Servicios", "Eventos / Catering"])

    st.divider()

    st.markdown("### 🏢 Tu Negocio (Vendedor)")
    seller_name = st.text_input("Tu Nombre o el de tu Negocio", value=profile.get("business_name", ""), placeholder="Ej: Talleres San José")
    uploaded_logo = None
    db_logo_url = profile.get("logo_url", "")
    
    if db_logo_url:
        st.success("✅ Logo cargado automáticamente.")
    else:
        uploaded_logo = st.file_uploader("Sube tu logo (PNG, JPG - Máx 2MB)", type=["png", "jpg", "jpeg"])

    st.divider()

    st.markdown("### 👤 Datos del Cliente")
    country_codes = {"🇬🇹 +502": "502", "🇸🇻 +503": "503", "🇭🇳 +504": "504", "🇲🇽 +52": "52", "🇺🇸 +1": "1", "Otra": ""}
    
    if st.session_state.user and st.session_state.clients:
        client_options = ["➕ Crear nuevo..."] + [c['name'] for c in st.session_state.clients]
        sel_c = st.selectbox("Buscar cliente:", client_options)
        
        if sel_c == "➕ Crear nuevo...":
            c_name = st.text_input("Nombre Cliente")
            col_cc, col_phone = st.columns([1, 2])
            with col_cc:
                selected_country = st.selectbox("País", list(country_codes.keys()))
                phone_prefix = country_codes[selected_country]
            with col_phone:
                c_phone = st.text_input("WhatsApp / Teléfono")
            c_nit = st.text_input("NIT / ID Fiscal")
        else:
            c_data = next(c for c in st.session_state.clients if c['name'] == sel_c)
            c_name = sel_c
            st.info("💡 Autocompletado desde tu base de clientes.")
            
            col_cc, col_phone = st.columns([1, 2])
            with col_cc:
                selected_country = st.selectbox("País", list(country_codes.keys()))
                phone_prefix = country_codes[selected_country]
            with col_phone:
                c_phone = st.text_input("WhatsApp / Teléfono", value=c_data.get('phone', ''))
            
            c_nit = st.text_input("NIT / ID Fiscal", value=c_data.get('nit', ''))
    else:
        c_name = st.text_input("Nombre Cliente", placeholder="Ej: Maria Lopez")
        col_cc, col_phone = st.columns([1, 2])
        with col_cc:
            selected_country = st.selectbox("País", list(country_codes.keys()))
            phone_prefix = country_codes[selected_country]
        with col_phone:
            c_phone = st.text_input("WhatsApp / Teléfono", placeholder="Ej: 55551234")
        c_nit = st.text_input("NIT / ID Fiscal", placeholder="Ej: 1234567-8")

    vehicle_desc, vehicle_plate = "", ""
    if template == "Taller Mecánico / Motos":
        st.divider()
        st.markdown("### 🚗 Datos del Vehículo")
        col_v1, col_v2 = st.columns(2)
        with col_v1:
            vehicle_desc = st.text_input("Marca, Modelo", placeholder="Ej: Toyota Hilux")
        with col_v2:
            vehicle_plate = st.text_input("Placas", placeholder="Ej: P-123ABC")

    st.divider()

    st.markdown("### 🛒 Productos o Servicios")
    catalog_options = ["Escribir manualmente..."]
    custom_catalog_map = {}
    
    if profile.get("catalog"):
        for item in profile["catalog"]:
            catalog_options.append(f"⭐ {item['desc']}")
            custom_catalog_map[f"⭐ {item['desc']}"] = item['price']

    if template == "General":
        catalog_options += ["Producto básico", "Producto premium", "Servicio general", "Envío / Delivery", "Descuento especial"]
    elif template == "Taller Mecánico / Motos":
        catalog_options += ["Diagnóstico general", "Servicio menor", "Servicio mayor", "Cambio de aceite y filtro", "Cambio de pastillas de freno", "Alineación y balanceo", "Revisión del sistema eléctrico", "Reparación de motor", "Limpieza de inyectores"]
    elif template == "Odontología / Dentista":
        catalog_options += ["Consulta de evaluación", "Limpieza dental (Profilaxis)", "Relleno blanco (Resina)", "Extracción simple", "Extracción de cordal", "Blanqueamiento dental", "Tratamiento de canales", "Radiografía panorámica"]
    elif template == "Clínica Médica":
        catalog_options += ["Consulta médica general", "Consulta con especialista", "Examen de laboratorio clínico", "Electrocardiograma", "Ultrasonido", "Certificado médico", "Aplicación de medicamento"]
    elif template == "Construcción / Carpintería":
        catalog_options += ["Mano de obra (por día)", "Mano de obra (por obra)", "Instalación (m2)", "Fabricación de mueble a medida", "Pintura interior/exterior (m2)", "Reparación estructural", "Supervisión de obra", "Materiales varios"]
    elif template == "Freelance / Servicios":
        catalog_options += ["Consultoría (por hora)", "Consultoría (por proyecto)", "Desarrollo de página web", "Diseño de logotipo", "Gestión de redes sociales (Mensual)", "Auditoría / Análisis", "Traducción de documentos"]
    elif template == "Eventos / Catering":
        catalog_options += ["Menú por persona (Básico)", "Menú por persona (Premium)", "Alquiler de salón", "Alquiler de sillas y mesas", "Servicio de meseros", "Decoración floral", "Pastel personalizado", "Equipo de sonido"]

    selected_item = st.selectbox("Selecciona un servicio:", catalog_options)
    auto_desc = selected_item.replace("⭐ ", "") if selected_item in custom_catalog_map else (selected_item if selected_item != "Escribir manualmente..." else "")
    auto_price = float(custom_catalog_map.get(selected_item, 0.0))

    item_desc = st.text_input("Descripción", value=auto_desc)
    col3, col4 = st.columns(2)
    item_qty = col3.number_input("Cant.", min_value=1, value=1)
    item_price = col4.number_input("Precio", min_value=0.0, value=auto_price)

    if st.button("➕ Agregar"):
        if item_desc and item_price > 0:
            st.session_state.cart.append({"desc": item_desc, "qty": item_qty, "price": item_price, "total": item_qty * item_price})
            st.session_state.pdf_ready = False
        else:
            st.warning("Completa los datos.")

    total = 0.0
    if st.session_state.cart:
        st.markdown("#### 📋 Tu Lista:")
        for i, it in enumerate(st.session_state.cart):
            col_c1, col_c2, col_c3 = st.columns([4, 2, 1])
            with col_c1:
                st.write(f"**{it['qty']}x** {it['desc']}")
            with col_c2:
                st.write(f"{currency} {it['total']:.2f}")
            with col_c3:
                if st.button("❌", key=f"del_{i}"):
                    st.session_state.cart.pop(i)
                    st.session_state.pdf_ready = False
                    st.rerun()
            total += it['total']

    st.divider()
    st.markdown(f"#### Total: {currency} {total:.2f}")
    
    require_advance = st.checkbox("Requerir Anticipo")
    advance_amount, balance_due = 0.0, total
    if require_advance and total > 0:
        advance_pct = st.slider("Porcentaje (%)", min_value=10, max_value=100, value=50, step=10) / 100.0
        advance_amount = total * advance_pct
        balance_due = total - advance_amount
        st.info(f"**Anticipo:** {currency} {advance_amount:.2f} | **Saldo:** {currency} {balance_due:.2f}")

    st.divider()

    if st.button("Preparar Cotización ✨", type="primary", use_container_width=True):
        if not c_name or not st.session_state.cart:
            st.error("Faltan datos.")
        else:
            display_phone = f"+{phone_prefix} {c_phone}" if c_phone and phone_prefix else c_phone
            
            q_data = {
                "date": datetime.now().strftime("%d/%m/%Y"), 
                "client_name": c_name, 
                "display_phone": display_phone, 
                "client_nit": c_nit, 
                "currency": "Q" if "Q" in currency else "$", 
                "cart": st.session_state.cart, 
                "grand_total": total, 
                "advance_amount": advance_amount, 
                "balance_due": balance_due,
                "vehicle_desc": vehicle_desc, 
                "vehicle_plate": vehicle_plate,
                "seller_name": seller_name, 
                "logo_file": uploaded_logo, 
                "logo_url": db_logo_url,
                "bank_name": profile.get('bank_name'), 
                "account_type": profile.get('account_type'),
                "account_number": profile.get('account_number'), 
                "account_name": profile.get('account_name'), 
                "terms": profile.get('terms_conditions')
            }
            
            st.session_state.pdf_bytes = generate_pdf(q_data)
            st.session_state.pdf_ready = True
            
            if c_phone:
                clean_phone = ''.join(filter(str.isdigit, c_phone))
                full_wa_number = f"{phone_prefix}{clean_phone}"
                wa_message = f"¡Hola {c_name}! 👋 Te comparto la cotización de {seller_name if seller_name else 'nuestro servicio'}. El total es de {currency} {total:.2f}. Quedo a las órdenes si tienes alguna duda."
                wa_encoded = urllib.parse.quote(wa_message)
                st.session_state.wa_url = f"https://wa.me/{full_wa_number}?text={wa_encoded}"
            else:
                st.session_state.wa_url = ""
            
            if st.session_state.user:
                try:
                    db_quote_data = q_data.copy()
                    db_quote_data.pop('logo_file', None)
                    
                    supabase.table("quotes").insert({
                        "user_id": st.session_state.user.id, 
                        "client_name": c_name, 
                        "total_amount": total, 
                        "currency": q_data["currency"], 
                        "quote_data": db_quote_data
                    }).execute()
                    
                    existing_client = next((c for c in st.session_state.clients if c['name'] == c_name), None)
                    client_payload = {"user_id": st.session_state.user.id, "name": c_name, "phone": c_phone, "nit": c_nit}
                    if existing_client:
                        client_payload["id"] = existing_client["id"]
                    supabase.table("clients").upsert(client_payload).execute()
                    
                    fetch_user_data(force=True)
                except Exception as e:
                    print(f"DB Error: {e}")
            
            st.balloons()

    if st.session_state.get('pdf_ready'):
        st.success(f"¡Cotización lista para {c_name}!")
        col_act1, col_act2 = st.columns(2)
        with col_act1:
            st.download_button(
                label="1️⃣ Descargar PDF", 
                data=st.session_state.pdf_bytes, 
                file_name=f"Cotizacion_{c_name.replace(' ', '_')}.pdf", 
                mime="application/pdf", 
                use_container_width=True
            )
        with col_act2:
            if st.session_state.get('wa_url'):
                st.link_button("2️⃣ Enviar WhatsApp 💬", st.session_state.wa_url, use_container_width=True)

# ==========================================
# 📊 PAGE: HISTORIAL
# ==========================================
def page_history():
    st.page_link(page_gen, label="Volver al Generador", icon="⬅️")
    st.title("🗂️ Historial")
    if not st.session_state.user:
        return st.warning("Inicia sesión para ver tu historial.")

    search = st.text_input("🔍 Buscar por nombre...", placeholder="Ej: Maria Lopez")
    
    try:
        query = supabase.table("quotes").select("*").eq("user_id", st.session_state.user.id)
        if search:
            query = query.ilike("client_name", f"%{search}%")
        quotes = query.order("created_at", desc=True).execute().data
    except:
        quotes = []

    if not quotes:
        return st.info("No hay cotizaciones.")

    for q in quotes:
        try:
            display_date = datetime.strptime(q['created_at'].split('T')[0], "%Y-%m-%d").strftime("%d/%m/%Y")
        except:
            display_date = "Fecha"

        with st.expander(f"📄 {display_date} | {q['client_name']} - {q.get('currency', 'Q')} {q['total_amount']:.2f}"):
            qd = q['quote_data']
            qd['logo_file'] = None 
            for item in qd.get('cart', []):
                st.write(f"- {item['qty']}x {item['desc']}")
            
            st.download_button("📥 Re-descargar PDF", generate_pdf(qd), f"Cotizacion_{q['client_name']}.pdf", key=f"dl_{q['id']}")

# ==========================================
# 👥 PAGE: CLIENTES
# ==========================================
def page_clients():
    st.page_link(page_gen, label="Volver al Generador", icon="⬅️")
    st.title("👥 Mis Clientes")
    if not st.session_state.user:
        return st.warning("Inicia sesión para ver tus clientes.")

    fetch_user_data()
    if not st.session_state.clients:
        return st.info("No tienes clientes guardados.")
    
    for c in st.session_state.clients:
        with st.container(border=True):
            with st.expander(f"👤 **{c['name']}**"):
                new_phone = st.text_input("Teléfono", value=c.get('phone', ''), key=f"p_{c['id']}")
                new_nit = st.text_input("NIT / ID Fiscal", value=c.get('nit', ''), key=f"n_{c['id']}")
                if st.button("💾 Guardar", key=f"btn_{c['id']}"):
                    supabase.table("clients").update({"phone": new_phone, "nit": new_nit}).eq("id", c['id']).execute()
                    fetch_user_data(force=True)
                    st.success("¡Actualizado!")
                    st.rerun()

# ==========================================
# ⚙️ PAGE: PERFIL
# ==========================================
def page_profile():
    st.page_link(page_gen, label="Volver al Generador", icon="⬅️")
    st.title("⚙️ Mi Perfil")
    if not st.session_state.user:
        return st.warning("Inicia sesión para configurar tu perfil.")

    fetch_user_data()
    profile = st.session_state.user_profile
    catalog = profile.get("catalog", [])
    
    with st.expander("🏢 Negocio, Logo y Banco", expanded=True):
        name = st.text_input("Nombre del Negocio", value=profile.get('business_name', ''))
        
        current_logo = profile.get("logo_url", "")
        if current_logo:
            st.image(current_logo, width=150, caption="Logo Actual")
        new_logo = st.file_uploader("Subir/Actualizar Logo (PNG, JPG - Máx 2MB)", type=["png", "jpg", "jpeg"])
        
        st.markdown("**🏦 Información de Pago**")
        guatemala_banks = ["Banco Industrial (BI)", "Banco de Desarrollo Rural (Banrural)", "Banco G&T Continental", "Banco Agromercantil de Guatemala (BAM)", "Banco de América Central (BAC Credomatic)", "Banco de los Trabajadores (Bantrab)", "El Crédito Hipotecario Nacional (CHN)", "Banco Promerica", "Banco Ficohsa", "Banco Inmobiliario", "Banco Internacional", "Banco de Antigua", "Banco Azteca", "Banco Cuscatlán", "Vivibanco", "Banco INV", "Banco Credicorp", "Banco Nexa", "Banco MultiMoney", "Citibank", "Cooperativa Micoope", "Otra..."]
        current_bank = profile.get('bank_name', '')
        bank_index = guatemala_banks.index(current_bank) if current_bank in guatemala_banks else 0
        bank_name = st.selectbox("Banco", guatemala_banks, index=bank_index)
        
        acc_type = st.radio("Tipo de Cuenta", ["Monetaria", "Ahorro"], index=0 if profile.get('account_type') == "Monetaria" else 1, horizontal=True)
        acc_num = st.text_input("Número de Cuenta", value=profile.get('account_number', ''))
        acc_name = st.text_input("Nombre en la Cuenta", value=profile.get('account_name', ''))
        
        st.markdown("**📜 Condiciones**")
        st.info("💡 **Ejemplos rápidos (Copia y pega):**\n"
                "**1. Servicios:** *Cotización válida por 15 días. Anticipo del 50% no reembolsable para agendar. Saldo al finalizar.*\n"
                "**2. Productos:** *Precios sujetos a cambios. Garantía de 30 días contra defectos de fábrica. No se aceptan devoluciones.*")
        terms = st.text_area("Condiciones", value=profile.get('terms_conditions', ''))
        
        if st.button("💾 Guardar Cambios", type="primary"):
            final_logo_url = current_logo
            if new_logo:
                file_ext = new_logo.name.split('.')[-1]
                file_path = f"{st.session_state.user.id}/logo_{int(datetime.now().timestamp())}.{file_ext}"
                try:
                    supabase.storage.from_("logos").upload(file=new_logo.getvalue(), path=file_path, file_options={"content-type": f"image/{file_ext}", "upsert": "true"})
                    final_logo_url = supabase.storage.from_("logos").get_public_url(file_path)
                except Exception as e:
                    st.error(f"Error: {e}")

            supabase.table("profiles").upsert({
                "id": st.session_state.user.id, 
                "business_name": name, 
                "logo_url": final_logo_url,
                "bank_name": bank_name, 
                "account_type": acc_type, 
                "account_number": acc_num,
                "account_name": acc_name, 
                "terms_conditions": terms, 
                "catalog": catalog
            }).execute()
            fetch_user_data(force=True)
            st.success("¡Guardado!")

    st.subheader("📚 Mi Catálogo")
    if catalog:
        for idx, item in enumerate(catalog):
            col1, col2, col3 = st.columns([4, 2, 1])
            col1.write(f"🔹 {item['desc']}")
            col2.write(f"{profile.get('currency', 'Q')} {item['price']}")
            if col3.button("❌", key=f"del_cat_{idx}"):
                catalog.pop(idx)
                supabase.table("profiles").upsert({**profile, "catalog": catalog}).execute()
                fetch_user_data(force=True)
                st.rerun()
    else:
        st.info("Catálogo vacío.")

    with st.container(border=True):
        new_desc = st.text_input("Servicio/Producto")
        new_price = st.number_input("Precio", min_value=0.0)
        if st.button("➕ Guardar en Catálogo"):
            if new_desc and new_price > 0:
                catalog.append({"desc": new_desc, "price": new_price})
                supabase.table("profiles").upsert({**profile, "catalog": catalog}).execute()
                fetch_user_data(force=True)
                st.success("Añadido.")
                st.rerun()

    st.subheader("🔒 Seguridad")
    with st.expander("Cambiar mi Contraseña"):
        st.info("Si usaste un enlace de recuperación, ingresa aquí tu nueva contraseña.")
        new_password = st.text_input("Nueva Contraseña", type="password")
        if st.button("Actualizar Contraseña"):
            if len(new_password) >= 6:
                try:
                    supabase.auth.update_user({"password": new_password})
                    st.success("✅ ¡Tu contraseña ha sido actualizada con éxito!")
                except Exception as e:
                    st.error(f"Error al actualizar: {e}")
            else:
                st.warning("La contraseña debe tener al menos 6 caracteres.")

# ==========================================
# 💬 PAGE: SOPORTE
# ==========================================
def page_support():
    st.page_link(page_gen, label="Volver al Generador", icon="⬅️")
    st.title("💬 Soporte y Feedback")
    
    with st.container(border=True):
        st.write("¿Tienes alguna duda, sugerencia o encontraste un error en CotiListo?")
        st.write("¡Escríbeme directamente! Me encantaría saber cómo puedo mejorar la herramienta para ti.")
        
        wa_number = "50259714667"
        wa_message = urllib.parse.quote("¡Hola Romain! Necesito ayuda o tengo un comentario sobre CotiListo: ")
        wa_url = f"https://wa.me/{wa_number}?text={wa_message}"
        
        st.info("💡 Tu feedback es vital para seguir haciendo crecer esta plataforma.")
        st.link_button("Contactar por WhatsApp 🟢", wa_url, use_container_width=True)

# ==========================================
# 🔐 PAGE: LOGIN
# ==========================================

# 1. Login processing function
def process_login():
    try:
        res = supabase.auth.sign_in_with_password({
            "email": st.session_state.login_email.strip(), 
            "password": st.session_state.login_pw
        })
        st.session_state.user = res.user
        fetch_user_data(force=True)
        st.session_state.show_welcome = True
        st.session_state.login_error = None
    except:
        st.session_state.login_error = "Credenciales incorrectas"

# 2. Registration processing function
def process_registration():
    try:
        supabase.auth.sign_up({
            "email": st.session_state.reg_email.strip(), 
            "password": st.session_state.reg_pw
        })
        st.session_state.reg_msg = "Cuenta creada con éxito. Ya puedes ingresar."
        st.session_state.reg_error = None
    except Exception as e:
        st.session_state.reg_error = f"Error: {e}"

# 3. Main Login Page UI
def page_login():
    st.page_link(page_gen, label="Volver al Generador", icon="⬅️")
    st.title("🔐 Acceso Premium")
    if not supabase:
        return st.error("Supabase error.")

    tab1, tab2 = st.tabs(["Ingresar", "Crear Cuenta"])
    
    with tab1:
        # Standard Login Form
        with st.form("login_form"):
            st.text_input("Email", key="login_email", autocomplete="email")
            st.text_input("Contraseña", type="password", key="login_pw", autocomplete="current-password")
            st.form_submit_button("Entrar", type="primary", use_container_width=True, on_click=process_login)
        
        if st.session_state.get("login_error"):
            st.error(st.session_state.login_error)
            st.session_state.login_error = None

        # Interactive "Forgot Password" Section (OTP Flow)
        st.write("") 
        with st.expander("¿Olvidaste tu contraseña?"):
            # Step 1: Request OTP Code via Email
            if not st.session_state.get("recovery_code_sent", False):
                st.markdown("<small>Ingresa tu email pour recibir un código de recuperación de 6 dígitos.</small>", unsafe_allow_html=True)
                reset_email = st.text_input("Tu Email", key="reset_email_input", label_visibility="collapsed", placeholder="ejemplo@correo.com")
                
                if st.button("Enviar código", use_container_width=True):
                    if reset_email:
                        try:
                            # Trigger Supabase recovery email with {{ .Token }}
                            supabase.auth.reset_password_email(reset_email.strip())
                            st.session_state.recovery_email = reset_email.strip()
                            st.session_state.recovery_code_sent = True
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")
                    else:
                        st.warning("Por favor, ingresa tu email.")
            
            # Step 2: Input OTP Code and set New Password
            else:
                st.success(f"📧 Código enviado a **{st.session_state.recovery_email}**")
                st.markdown("<small>Ingresa el código de 6 dígitos de tu correo y ton nueva contraseña.</small>", unsafe_allow_html=True)
                
                recovery_code = st.text_input("Código de 6 dígitos")
                new_pw = st.text_input("Nueva Contraseña", type="password")
                
                col_btn1, col_btn2 = st.columns(2)
                
                if col_btn1.button("Cambiar Contraseña", type="primary", use_container_width=True):
                    if recovery_code and len(new_pw) >= 6:
                        try:
                            # Verify the numerical token
                            res = supabase.auth.verify_otp({
                                "email": st.session_state.recovery_email,
                                "token": recovery_code,
                                "type": "recovery"
                            })
                            
                            # Update user password now that they are authenticated
                            supabase.auth.update_user({"password": new_pw})
                            
                            # Success: Clean up and log in
                            st.session_state.recovery_code_sent = False
                            st.session_state.user = res.user
                            fetch_user_data(force=True)
                            st.session_state.show_welcome = True
                            st.rerun()
                        except Exception as e:
                            st.error("El código es incorrecto o ha expirado.")
                    else:
                        st.warning("Ingresa le código y una contraseña de al menos 6 caracteres.")
                        
                if col_btn2.button("Cancelar", use_container_width=True):
                    st.session_state.recovery_code_sent = False
                    st.rerun()

    with tab2:
        # Account Registration Form
        with st.form("register_form"):
            st.text_input("Tu Email", key="reg_email")
            st.text_input("Crea una Contraseña", type="password", key="reg_pw") 
            st.form_submit_button("Registrarme", use_container_width=True, on_click=process_registration)
        
        if st.session_state.get("reg_msg"):
            st.success(st.session_state.reg_msg)
            st.session_state.reg_msg = None
        if st.session_state.get("reg_error"):
            st.error(st.session_state.reg_error)
            st.session_state.reg_error = None

# ==========================================
# 🧭 NAVIGATION & APP ROUTING
# ==========================================
page_gen = st.Page(page_free_generator, title="Generador", icon="📝")
page_hist = st.Page(page_history, title="Historial", icon="🗂️")
page_crm = st.Page(page_clients, title="Mis Clientes", icon="👥")
page_prof = st.Page(page_profile, title="Mi Perfil", icon="⚙️")
page_sup = st.Page(page_support, title="Soporte", icon="💬")
page_log = st.Page(page_login, title="Entrar / Registro", icon="🔐")

if st.session_state.user:
    with st.sidebar:
        st.write(f"👤 {st.session_state.user.email}")
        if st.button("🚪 Cerrar Sesión", use_container_width=True): 
            supabase.auth.sign_out()
            st.session_state.user = None
            st.session_state.user_profile = {}
            st.session_state.clients = []
            st.rerun()
    pg = st.navigation([page_gen, page_hist, page_crm, page_prof, page_sup])
else:
    pg = st.navigation([page_gen, page_sup, page_log])

pg.run()