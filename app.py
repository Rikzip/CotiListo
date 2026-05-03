import streamlit as st
import io
import urllib.parse
import os
import base64
import textwrap
import json
from datetime import datetime, timezone, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
import urllib.request
from supabase import create_client, Client, ClientOptions
from PIL import Image
import logging
import streamlit.components.v1 as components

# ==========================================
# 📱 GLOBAL CONFIGURATION
# ==========================================
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("APP_BASE_URL", "https://app.cotilisto.com")
PDF_LINK_EXPIRY_DAYS = 30
PDF_SIGNED_URL_SECONDS = PDF_LINK_EXPIRY_DAYS * 86400
IVA_RATE = 0.12
COOKIE_NAME = "cotilisto_session"
COOKIE_EXPIRY_DAYS = 30

st.set_page_config(
    page_title="CotiListo - Cotizaciones",
    page_icon="favicon.png",
    layout="centered",
    initial_sidebar_state="auto"
)

st.markdown("""
    <style>
    .block-container { padding-top: 3rem !important; padding-bottom: 1rem !important; }
    [data-testid="stDecoration"] { display: none !important; }
    footer { display: none !important; }
    .stDeployButton { display: none !important; }
    [data-testid="stAppDeployButton"] { display: none !important; }
    [data-testid="stMainMenu"] { visibility: hidden !important; }
    [data-testid="stHeaderActionElements"] { display: none !important; }
    input::-ms-reveal, input::-ms-clear { display: none !important; }
    [data-testid="stFileUploadDropzone"] div div::before {
        content: "Arrastra y suelta tu logo aquí"; color: #555; display: block; margin-bottom: 5px;
    }
    [data-testid="stFileUploadDropzone"] div div span,
    [data-testid="stFileUploadDropzone"] small { display: none !important; }
    [data-testid="stFileUploadDropzone"] button::before {
        content: "Buscar archivo"; display: block; position: absolute;
        top: 50%; left: 50%; transform: translate(-50%, -50%); width: 100%;
    }
    [data-testid="stFileUploadDropzone"] button span { visibility: hidden; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 📱 PWA & MOBILE OPTIMIZATION
# ==========================================
def inject_pwa_metadata():
    components.html("""
        <script>
        const doc = window.parent.document;
        const metaTags = [
            {name: 'apple-mobile-web-app-title',           content: 'CotiListo'},
            {name: 'application-name',                      content: 'CotiListo'},
            {name: 'apple-mobile-web-app-capable',          content: 'yes'},
            {name: 'apple-mobile-web-app-status-bar-style', content: 'default'},
            {name: 'theme-color',                           content: '#0F529B'},
            {name: 'viewport',                              content: 'width=device-width, initial-scale=1, maximum-scale=1'}
        ];
        metaTags.forEach(tag => {
            let el = doc.querySelector(`meta[name="${tag.name}"]`);
            if (!el) { el = doc.createElement('meta'); el.name = tag.name; doc.head.appendChild(el); }
            el.content = tag.content;
        });
        </script>
    """, height=0, scrolling=False)

inject_pwa_metadata()

# ==========================================
# 🔑 SUPABASE CONNECTION
# ==========================================
@st.cache_resource
def get_supabase_client():
    url = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY", "")
    if url and key:
        return create_client(url, key, options=ClientOptions(flow_type="pkce"))
    return None

supabase = get_supabase_client()

# ==========================================
# 🍪 PERSISTENT SESSION (COOKIE-BASED)
# ==========================================
try:
    import extra_streamlit_components as stx
    cookie_manager = stx.CookieManager(key="cotilisto_cookies")
    COOKIES_AVAILABLE = True
except Exception:
    cookie_manager = None
    COOKIES_AVAILABLE = False


def save_session_cookie(access_token: str, refresh_token: str):
    """Save Supabase tokens to a persistent cookie."""
    if not COOKIES_AVAILABLE or not cookie_manager:
        return
    try:
        session_data = json.dumps({
            "access_token": access_token,
            "refresh_token": refresh_token,
        })
        expiry = datetime.now() + timedelta(days=COOKIE_EXPIRY_DAYS)
        cookie_manager.set(COOKIE_NAME, session_data, expires_at=expiry)
    except Exception as e:
        logger.warning(f"Cookie save failed: {e}")


def delete_session_cookie():
    """Delete the persistent session cookie."""
    if not COOKIES_AVAILABLE or not cookie_manager:
        return
    try:
        cookie_manager.delete(COOKIE_NAME)
    except Exception as e:
        logger.warning(f"Cookie delete failed: {e}")


def restore_session_from_cookie() -> bool:
    """Try to restore Supabase session from cookie. Returns True if successful."""
    if not COOKIES_AVAILABLE or not cookie_manager or not supabase:
        return False
    try:
        raw = cookie_manager.get(COOKIE_NAME)
        if not raw:
            return False
        data = json.loads(raw)
        access_token = data.get("access_token", "")
        refresh_token = data.get("refresh_token", "")
        if not access_token or not refresh_token:
            return False

        # Try to set session — if access_token expired, refresh it
        try:
            result = supabase.auth.set_session(access_token, refresh_token)
            if result and result.user:
                st.session_state.user = result.user
                # Refresh cookie with potentially new tokens
                if result.session:
                    save_session_cookie(result.session.access_token, result.session.refresh_token)
                return True
        except Exception:
            # access_token expired — try refresh
            try:
                result = supabase.auth.refresh_session(refresh_token)
                if result and result.user:
                    st.session_state.user = result.user
                    if result.session:
                        save_session_cookie(result.session.access_token, result.session.refresh_token)
                    return True
            except Exception as e:
                logger.warning(f"Session refresh failed: {e}")
                delete_session_cookie()
                return False
    except Exception as e:
        logger.warning(f"Cookie restore failed: {e}")
        return False

# ==========================================
# 📋 CONSTANTS & TEMPLATES
# ==========================================
TEMPLATES = {
    "General": ["Producto básico", "Producto premium", "Servicio general", "Envío / Delivery", "Descuento especial"],
    "Taller Mecánico / Motos": ["Diagnóstico general", "Servicio menor", "Servicio mayor", "Cambio de aceite y filtro", "Cambio de pastillas de freno", "Alineación y balanceo", "Revisión del sistema eléctrico", "Reparación de motor", "Limpieza de inyectores"],
    "Odontología / Dentista": ["Consulta de evaluación", "Limpieza dental (Profilaxis)", "Relleno blanco (Resina)", "Extracción simple", "Extracción de cordal", "Blanqueamiento dental", "Tratamiento de canales", "Radiografía panorámica"],
    "Clínica Médica": ["Consulta médica general", "Consulta con especialista", "Examen de laboratorio clínico", "Electrocardiograma", "Ultrasonido", "Certificado médico", "Aplicación de medicamento"],
    "Construcción / Carpintería": ["Mano de obra (por día)", "Mano de obra (por obra)", "Instalación (m2)", "Fabricación de mueble a medida", "Pintura interior/exterior (m2)", "Reparación estructural", "Supervisión de obra", "Materiales varios"],
    "Freelance / Servicios": ["Consultoría (por hora)", "Consultoría (por proyecto)", "Desarrollo de página web", "Diseño de logotipo", "Gestión de redes sociales (Mensual)", "Auditoría / Análisis", "Traducción de documentos"],
    "Eventos / Catering": ["Menú por persona (Básico)", "Menú por persona (Premium)", "Alquiler de salón", "Alquiler de sillas y mesas", "Servicio de meseros", "Decoración floral", "Pastel personalizado", "Equipo de sonido"],
}

COUNTRY_CODES = {
    "🇬🇹 +502": "502", "🇸🇻 +503": "503", "🇭🇳 +504": "504",
    "🇲🇽 +52": "52", "🇺🇸 +1": "1", "Otra": "",
}

GUATEMALA_BANKS = [
    "Banco Industrial (BI)", "Banco de Desarrollo Rural (Banrural)", "Banco G&T Continental",
    "Banco Agromercantil de Guatemala (BAM)", "Banco de América Central (BAC Credomatic)",
    "Banco de los Trabajadores (Bantrab)", "El Crédito Hipotecario Nacional (CHN)",
    "Banco Promerica", "Banco Ficohsa", "Banco Inmobiliario", "Banco Internacional",
    "Banco de Antigua", "Banco Azteca", "Banco Cuscatlán", "Vivibanco", "Banco INV",
    "Banco Credicorp", "Banco Nexa", "Banco MultiMoney", "Citibank",
    "Cooperativa Micoope", "Otra...",
]

# ==========================================
# 🌍 PUBLIC ROUTER: THE VIEWER PAGE
# ==========================================
query_params = st.query_params.to_dict()

if "doc" in query_params:
    doc_id = query_params["doc"]
    st.empty()

    if os.path.exists("logo_cotilisto.png"):
        try:
            with open("logo_cotilisto.png", "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            st.markdown(
                f'<div style="text-align:center;padding:10px 0 20px 0;">'
                f'<img src="data:image/png;base64,{img_b64}" width="130"></div>',
                unsafe_allow_html=True
            )
        except Exception:
            st.markdown("<h3 style='text-align:center;'>CotiListo</h3>", unsafe_allow_html=True)
    else:
        st.markdown("<h3 style='text-align:center;'>CotiListo</h3>", unsafe_allow_html=True)

    try:
        if supabase:
            res = supabase.table("quotes").select("*").eq("id", doc_id).execute()
            if res.data:
                quote = res.data[0]
                created_date = datetime.fromisoformat(quote['created_at'].replace("Z", "+00:00"))
                days_old = (datetime.now(timezone.utc) - created_date).days

                # Increment view counter silently
                try:
                    supabase.table("quotes").update(
                        {"views_count": (quote.get("views_count") or 0) + 1}
                    ).eq("id", doc_id).execute()
                except Exception:
                    pass

                q_num = quote.get("quote_number", "")
                title_suffix = f" — {q_num}" if q_num else ""
                st.title(f"📄 Cotización para {quote['client_name']}{title_suffix}")

                if days_old > PDF_LINK_EXPIRY_DAYS:
                    st.error("⚠️ Documento Expirado")
                    st.warning(
                        "Por seguridad y posible actualización de precios, este enlace ha expirado "
                        f"tras {PDF_LINK_EXPIRY_DAYS} días. Por favor, contacta a tu asesor para "
                        "solicitar una versión actualizada."
                    )
                else:
                    st.write(f"**Total:** {quote['currency']} {float(quote['total_amount']):.2f}")
                    pdf_url = quote.get("pdf_url")
                    if pdf_url:
                        st.info("💡 Tu documento está listo y asegurado en la nube.")
                        st.link_button("📄 Abrir / Descargar PDF", pdf_url, type="primary", use_container_width=True)
                    else:
                        st.warning("El documento ya no está disponible.")
            else:
                st.error("Documento no encontrado o enlace inválido.")
        else:
            st.error("Error de configuración de base de datos.")
    except Exception as e:
        logger.error(f"Viewer error: {e}")
        st.error("Error de conexión. Intenta de nuevo más tarde.")

    st.stop()

# ==========================================
# 🧠 SESSION STATE INITIALIZATION
# ==========================================
_defaults = {
    'cart': [],
    'pdf_ready': False,
    'pdf_bytes': None,
    'wa_url': "",
    'smart_url': "",
    'last_client_name': "",
    'last_client_email': "",
    'user': None,
    'user_profile': {},
    'clients': [],
    'quotes_this_month': 0,
    'total_ganado': 0.0,
    'quotes_won_count': 0,
    'total_quotes_count': 0,
    'show_welcome': False,
    'login_error': None,
    'reg_msg': None,
    'reg_error': None,
    'recovery_code_sent': False,
    'recovery_email': "",
    'password_changed_success': False,
    'user_data_loaded': False,
    'profile_saved': False,
    'catalog_saved': False,
    'session_restored': False,
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ==========================================
# 🍪 AUTO-RESTORE SESSION ON APP LOAD
# ==========================================
if not st.session_state.user and not st.session_state.session_restored:
    st.session_state.session_restored = True
    if restore_session_from_cookie():
        st.session_state.show_welcome = True

# ==========================================
# ⚙️ HELPERS
# ==========================================
@st.cache_data
def get_base64_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def fetch_user_data(force: bool = False):
    if not st.session_state.user:
        return
    uid = st.session_state.user.id

    if force or not st.session_state.get('user_data_loaded'):
        try:
            res = supabase.table("profiles").select("*").eq("id", uid).execute()
            st.session_state.user_profile = res.data[0] if res.data else {}
        except Exception as e:
            logger.error(f"Profile fetch error: {e}")

        try:
            res = supabase.table("clients").select("*").eq("user_id", uid).order("name").execute()
            st.session_state.clients = res.data if res.data else []
        except Exception as e:
            logger.error(f"Clients fetch error: {e}")

        # Monthly quote count
        try:
            now = datetime.now(timezone.utc)
            first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
            res_count = supabase.table("quotes").select("id", count="exact") \
                .eq("user_id", uid).gte("created_at", first_day).execute()
            st.session_state.quotes_this_month = res_count.count or 0
        except Exception as e:
            logger.warning(f"Quote count error: {e}")

        # Total quotes count (for quote_number generation)
        try:
            res_total = supabase.table("quotes").select("id", count="exact") \
                .eq("user_id", uid).execute()
            st.session_state.total_quotes_count = res_total.count or 0
        except Exception as e:
            logger.warning(f"Total quote count error: {e}")

        # All-time won revenue
        try:
            res_won = supabase.table("quotes").select("total_amount") \
                .eq("user_id", uid).eq("status", "ganada").execute()
            st.session_state.total_ganado = sum(
                float(q['total_amount']) for q in res_won.data
            ) if res_won.data else 0.0
            st.session_state.quotes_won_count = len(res_won.data) if res_won.data else 0
        except Exception as e:
            logger.warning(f"Won quotes error: {e}")

        st.session_state.user_data_loaded = True


def get_quote_number(user_id: str, year: int, total_count: int) -> str:
    """Generate sequential quote number per user: COT-2026-0042"""
    next_num = total_count + 1
    return f"COT-{year}-{next_num:04d}"


def process_image_for_pdf(image_input_stream) -> io.BytesIO:
    try:
        img = Image.open(image_input_stream)
        if img.mode in ('RGBA', 'LA'):
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        target_width = 500
        if img.width > target_width:
            ratio = target_width / float(img.width)
            img = img.resize((target_width, int(img.height * ratio)), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format='JPEG', quality=75, optimize=True)
        out.seek(0)
        return out
    except Exception as e:
        logger.warning(f"Image processing failed: {e}")
        image_input_stream.seek(0)
        return image_input_stream


def upload_pdf_to_storage(pdf_bytes: bytes, user_id: str) -> tuple[str, str]:
    file_name = f"{user_id}/cotizacion_{int(datetime.now().timestamp())}.pdf"
    supabase.storage.from_("quotations").upload(
        file=pdf_bytes, path=file_name,
        file_options={"content-type": "application/pdf", "upsert": "true"}
    )
    try:
        signed = supabase.storage.from_("quotations").create_signed_url(file_name, PDF_SIGNED_URL_SECONDS)
        secure_url = signed.get("signedURL") or signed.get("signed_url", "")
    except Exception as e:
        logger.warning(f"Signed URL failed: {e}")
        secure_url = ""
    public_url = supabase.storage.from_("quotations").get_public_url(file_name)
    return public_url, secure_url or public_url


def _purge_expired_pdfs(user_id: str, quotes: list):
    for q in quotes:
        created_date = datetime.fromisoformat(q['created_at'].replace("Z", "+00:00"))
        days_old = (datetime.now(timezone.utc) - created_date).days
        if days_old > PDF_LINK_EXPIRY_DAYS and q.get("pdf_url"):
            try:
                old_path = q["pdf_url"].split("/quotations/")[1].split("?")[0]
                supabase.storage.from_("quotations").remove([old_path])
                supabase.table("quotes").update({"pdf_url": None}).eq("id", q["id"]).execute()
            except Exception as e:
                logger.warning(f"Purge failed for quote {q['id']}: {e}")


# ==========================================
# ⚙️ PDF GENERATION ENGINE
# ==========================================
def _draw_header(c, width, height, quote_data):
    header_height = 1.4 * inch
    header_y = height - header_height
    c.setFillColorRGB(0.97, 0.97, 0.97)
    c.rect(0, header_y, width, header_height, fill=True, stroke=False)
    c.setFillColorRGB(0.09, 0.44, 0.76)
    c.rect(0, header_y, width, 0.03 * inch, fill=True, stroke=False)

    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.setFont("Helvetica-Bold", 20)
    c.drawRightString(width - 0.75 * inch, height - 0.58 * inch, "Cotización")

    # Quote number
    if quote_data.get('quote_number'):
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(0.09, 0.44, 0.76)
        c.drawRightString(width - 0.75 * inch, height - 0.76 * inch, quote_data['quote_number'])

    c.setFont("Helvetica", 9)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawRightString(width - 0.75 * inch, height - 0.92 * inch, f"Fecha: {quote_data['date']}")

    # Validity date
    if quote_data.get('validity_date'):
        c.drawRightString(width - 0.75 * inch, height - 1.06 * inch, f"Válida hasta: {quote_data['validity_date']}")

    c.setFillColorRGB(0, 0, 0)

    logo_w, logo_h = 2.0 * inch, 1.0 * inch
    logo_x = 0.75 * inch
    logo_y = header_y + (header_height - logo_h) / 2
    logo_drawn = False

    image_stream = None
    if quote_data.get('logo_file'):
        image_stream = process_image_for_pdf(quote_data['logo_file'])
    elif quote_data.get('logo_url'):
        try:
            req = urllib.request.Request(quote_data['logo_url'], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                image_stream = process_image_for_pdf(io.BytesIO(response.read()))
        except Exception as e:
            logger.warning(f"Logo download failed: {e}")

    if image_stream:
        try:
            c.drawImage(
                ImageReader(image_stream), logo_x, logo_y,
                width=logo_w, height=logo_h,
                preserveAspectRatio=True, mask='auto'
            )
            logo_drawn = True
        except Exception as e:
            logger.warning(f"Logo draw failed: {e}")

    if not logo_drawn and quote_data.get('seller_name'):
        c.setFont("Helvetica-Bold", 15)
        c.setFillColorRGB(0.2, 0.2, 0.2)
        c.drawString(logo_x, header_y + (header_height / 2) - 0.1 * inch, quote_data['seller_name'])
        c.setFillColorRGB(0, 0, 0)


def _draw_client_info(c, y_pos, quote_data) -> float:
    c.setFont("Helvetica-Bold", 11)
    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.drawString(1 * inch, y_pos, f"Cliente: {quote_data['client_name']}")
    c.setFillColorRGB(0, 0, 0)
    y_pos -= 0.22 * inch

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

    return y_pos


def _draw_items_table(c, y_pos, quote_data, width, height) -> float:
    y_pos -= 0.4 * inch

    def draw_table_header(y):
        c.setFillColorRGB(0.09, 0.44, 0.76)
        c.rect(1 * inch, y - 0.08 * inch, 6.5 * inch, 0.28 * inch, fill=True, stroke=False)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(1.1 * inch, y, "Descripción")
        c.drawString(4.5 * inch, y, "Cant.")
        c.drawString(5.5 * inch, y, f"Precio ({quote_data['currency']})")
        c.setFillColorRGB(0, 0, 0)
        return y - 0.3 * inch

    y_pos = draw_table_header(y_pos)
    c.setFont("Helvetica", 10)

    for idx, item in enumerate(quote_data['cart']):
        if y_pos < 2.5 * inch:
            c.showPage()
            y_pos = height - 1 * inch
            y_pos = draw_table_header(y_pos)
            c.setFont("Helvetica", 10)

        if idx % 2 == 0:
            c.setFillColorRGB(0.98, 0.98, 0.98)
            c.rect(1 * inch, y_pos - 0.06 * inch, 6.5 * inch, 0.22 * inch, fill=True, stroke=False)
            c.setFillColorRGB(0, 0, 0)

        c.drawString(1.1 * inch, y_pos, item['desc'][:45])
        c.drawString(4.5 * inch, y_pos, str(item['qty']))
        c.drawString(5.5 * inch, y_pos, f"{item['total']:.2f}")
        y_pos -= 0.25 * inch

    return y_pos


def _draw_totals(c, y_pos, quote_data) -> float:
    y_pos -= 0.1 * inch
    c.setStrokeColorRGB(0.09, 0.44, 0.76)
    c.line(4 * inch, y_pos, 7.5 * inch, y_pos)
    c.setStrokeColorRGB(0, 0, 0)
    y_pos -= 0.25 * inch

    subtotal = quote_data['subtotal']
    discount_amount = quote_data.get('discount_amount', 0.0)
    iva_amount = quote_data.get('iva_amount', 0.0)
    grand_total = quote_data['grand_total']
    currency = quote_data['currency']

    c.setFont("Helvetica", 10)

    # Subtotal
    c.drawString(4.2 * inch, y_pos, "Subtotal:")
    c.drawString(5.5 * inch, y_pos, f"{currency} {subtotal:.2f}")
    y_pos -= 0.22 * inch

    # Discount
    if discount_amount > 0:
        c.setFillColorRGB(0.8, 0.1, 0.1)
        c.drawString(4.2 * inch, y_pos, "Descuento:")
        c.drawString(5.5 * inch, y_pos, f"- {currency} {discount_amount:.2f}")
        c.setFillColorRGB(0, 0, 0)
        y_pos -= 0.22 * inch

    # IVA
    if iva_amount > 0:
        c.drawString(4.2 * inch, y_pos, f"IVA ({int(IVA_RATE * 100)}%):")
        c.drawString(5.5 * inch, y_pos, f"{currency} {iva_amount:.2f}")
        y_pos -= 0.22 * inch

    # Total
    y_pos -= 0.05 * inch
    c.setFont("Helvetica-Bold", 11)
    c.setFillColorRGB(0.09, 0.44, 0.76)
    c.drawString(4.2 * inch, y_pos, "TOTAL:")
    c.drawString(5.5 * inch, y_pos, f"{currency} {grand_total:.2f}")
    c.setFillColorRGB(0, 0, 0)
    y_pos -= 0.28 * inch

    # Advance
    if quote_data.get('advance_amount', 0) > 0:
        c.setFont("Helvetica", 10)
        c.drawString(4.2 * inch, y_pos, "Anticipo Requerido:")
        c.drawString(5.5 * inch, y_pos, f"{currency} {quote_data['advance_amount']:.2f}")
        y_pos -= 0.2 * inch
        c.setFont("Helvetica-Bold", 10)
        c.drawString(4.2 * inch, y_pos, "Saldo a Pagar:")
        c.drawString(5.5 * inch, y_pos, f"{currency} {quote_data['balance_due']:.2f}")

    return y_pos


def _draw_footer(c, width, quote_data):
    y_pos = 1.8 * inch
    c.setStrokeColorRGB(0.8, 0.8, 0.8)
    c.line(1 * inch, y_pos, 7.5 * inch, y_pos)
    c.setStrokeColorRGB(0, 0, 0)
    y_pos -= 0.2 * inch

    if quote_data.get('bank_name') and quote_data.get('account_number'):
        c.setFont("Helvetica-Bold", 9)
        c.drawString(1 * inch, y_pos, "Información de Pago:")
        c.setFont("Helvetica", 9)
        bank_str = (
            f"{quote_data['bank_name']} - Cuenta "
            f"{quote_data.get('account_type', 'Monetaria')} "
            f"No. {quote_data['account_number']}"
        )
        if quote_data.get('account_name'):
            bank_str += f" (A nombre de: {quote_data['account_name']})"
        for line in textwrap.wrap(bank_str, width=75):
            c.drawString(2.6 * inch, y_pos, line)
            y_pos -= 0.15 * inch
    else:
        y_pos -= 0.15 * inch

    y_pos -= 0.05 * inch

    if quote_data.get('terms'):
        c.setFont("Helvetica-Bold", 9)
        c.drawString(1 * inch, y_pos, "Condiciones:")
        c.setFont("Helvetica", 9)
        for line in textwrap.wrap(quote_data['terms'], width=75):
            c.drawString(2.6 * inch, y_pos, line)
            y_pos -= 0.15 * inch

    c.setFont("Helvetica-Oblique", 9)
    c.setFillColorRGB(0.6, 0.6, 0.6)
    promo = "Generado con CotiListo.com"
    tw = c.stringWidth(promo, "Helvetica-Oblique", 9)
    c.drawString((width / 2) - (tw / 2), 0.5 * inch, promo)


def generate_pdf(quote_data: dict) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    _draw_header(c, width, height, quote_data)
    y_pos = _draw_client_info(c, height - 1.7 * inch, quote_data)
    y_pos = _draw_items_table(c, y_pos, quote_data, width, height)
    _draw_totals(c, y_pos, quote_data)
    _draw_footer(c, width, quote_data)
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
        st.markdown(
            f'<div style="text-align:center;margin-top:0px;padding-bottom:5px;">'
            f'<img src="data:image/png;base64,{img_b64}" width="160"></div>',
            unsafe_allow_html=True
        )
    else:
        st.markdown("<h1 style='text-align:center;margin-top:0px;'>CotiListo</h1>", unsafe_allow_html=True)

    st.markdown(
        "<h3 style='text-align:center;color:#555;margin-top:0px;margin-bottom:20px;'>"
        "Crea tu cotización en segundos</h3>",
        unsafe_allow_html=True
    )

    if not st.session_state.user:
        with st.container(border=True):
            st.markdown("#### ⚡ Trabaja más rápido")
            st.write("Logo automático, catálogo de precios, historial y enlace WhatsApp inteligente.")
            col_cta1, col_cta2 = st.columns(2)
            with col_cta1:
                st.page_link(page_log, label="✨ Crear cuenta gratis")
            with col_cta2:
                st.page_link(page_log, label="🔑 Ya tengo cuenta")

    # --- Currency, template & metrics ---
    if st.session_state.user:
        default_currency = profile.get("default_currency", "Q")
        col1, col2 = st.columns([1, 2])
        currency = col1.radio("Moneda:", ["Q", "$"], index=0 if default_currency == "Q" else 1, horizontal=True)

        # Metrics
        total_ganado = st.session_state.total_ganado
        currency_symbol = "Q" if "Q" in currency else "$"
        col2.metric("📊 Este mes", f"{st.session_state.quotes_this_month} cotizaciones")

        with st.container(border=True):
            m1, m2 = st.columns(2)
            m1.metric("🏆 Total ganado", f"{currency_symbol} {total_ganado:,.0f}")
            m2.metric("✅ Contratos", f"{st.session_state.quotes_won_count}")
            if total_ganado == 0 and st.session_state.quotes_this_month > 0:
                st.caption("¿Cerraste un trato? Márcalo como ganado en tu Historial →")
                st.page_link(page_hist, label="Ir al Historial", icon="🗂️")

        template = "Personalizado"
        st.info(f"✨ Modo personalizado: **{profile.get('business_name', 'tu negocio')}**")
    else:
        col1, col2 = st.columns(2)
        currency = col1.radio("Moneda:", ["Q", "$"], horizontal=True)
        template = col2.selectbox("Tipo de negocio:", list(TEMPLATES.keys()))

    st.divider()

    # --- Seller info ---
    st.markdown("### 🏢 Tu Negocio (Vendedor)")
    seller_name = st.text_input(
        "Tu Nombre o el de tu Negocio",
        value=profile.get("business_name", ""),
        placeholder="Ej: Talleres San José"
    )
    uploaded_logo = None
    db_logo_url = profile.get("logo_url", "")

    if db_logo_url:
        st.success("✅ Logo cargado automáticamente.")
    else:
        uploaded_logo = st.file_uploader("Sube tu logo (PNG, JPG - Máx 2MB)", type=["png", "jpg", "jpeg"])
        if uploaded_logo:
            st.image(uploaded_logo, width=100, caption="Logo que aparecerá en tu PDF")

    st.divider()

    # --- Client info ---
    st.markdown("### 👤 Datos del Cliente")
    c_name = ""
    c_phone = ""
    c_email = ""
    c_nit = ""
    phone_prefix = "502"

    if st.session_state.user and st.session_state.clients:
        client_options = ["➕ Crear nuevo..."] + [c['name'] for c in st.session_state.clients]
        sel_c = st.selectbox("Buscar cliente:", client_options)

        if sel_c == "➕ Crear nuevo...":
            c_name = st.text_input("Nombre Cliente")
            col_cc, col_phone_input = st.columns([1, 2])
            selected_country = col_cc.selectbox("País", list(COUNTRY_CODES.keys()))
            phone_prefix = COUNTRY_CODES[selected_country]
            c_phone = col_phone_input.text_input("WhatsApp / Teléfono")
            col_email_input, col_nit_input = st.columns(2)
            c_email = col_email_input.text_input("Email (Opcional)", placeholder="ejemplo@correo.com")
            c_nit = col_nit_input.text_input("NIT / ID Fiscal")
        else:
            c_data = next((c for c in st.session_state.clients if c['name'] == sel_c), {})
            c_name = sel_c
            st.info("💡 Autocompletado desde tu base de clientes.")
            col_cc, col_phone_input = st.columns([1, 2])
            selected_country = col_cc.selectbox("País", list(COUNTRY_CODES.keys()))
            phone_prefix = COUNTRY_CODES[selected_country]
            c_phone = col_phone_input.text_input("WhatsApp / Teléfono", value=c_data.get('phone', ''))
            col_email_input, col_nit_input = st.columns(2)
            c_email = col_email_input.text_input("Email (Opcional)", value=c_data.get('email', ''))
            c_nit = col_nit_input.text_input("NIT / ID Fiscal", value=c_data.get('nit', ''))
    else:
        c_name = st.text_input("Nombre Cliente", placeholder="Ej: Maria Lopez")
        col_cc, col_phone_input = st.columns([1, 2])
        selected_country = col_cc.selectbox("País", list(COUNTRY_CODES.keys()))
        phone_prefix = COUNTRY_CODES[selected_country]
        c_phone = col_phone_input.text_input("WhatsApp / Teléfono", placeholder="Ej: 55551234")
        col_email_input, col_nit_input = st.columns(2)
        c_email = col_email_input.text_input("Email (Opcional)", placeholder="ejemplo@correo.com")
        c_nit = col_nit_input.text_input("NIT / ID Fiscal", placeholder="Ej: 1234567-8")

    # --- Vehicle fields ---
    vehicle_desc, vehicle_plate = "", ""
    if not st.session_state.user:
        if template == "Taller Mecánico / Motos":
            st.divider()
            st.markdown("### 🚗 Datos del Vehículo")
            col_v1, col_v2 = st.columns(2)
            vehicle_desc = col_v1.text_input("Marca, Modelo", placeholder="Ej: Toyota Hilux")
            vehicle_plate = col_v2.text_input("Placas", placeholder="Ej: P-123ABC")
    else:
        st.divider()
        st.markdown("### 🚗 Datos del Vehículo (Opcional)")
        col_v1, col_v2 = st.columns(2)
        vehicle_desc = col_v1.text_input("Marca, Modelo", placeholder="Ej: Toyota Hilux")
        vehicle_plate = col_v2.text_input("Placas", placeholder="Ej: P-123ABC")

    st.divider()

    # --- Items ---
    st.markdown("### 🛒 Productos o Servicios")
    catalog_options = ["Escribir manualmente..."]
    custom_catalog_map = {}

    if profile.get("catalog"):
        for item in profile["catalog"]:
            label = f"⭐ {item['desc']}"
            catalog_options.append(label)
            custom_catalog_map[label] = item['price']

    catalog_options += TEMPLATES.get(template, [])
    selected_item = st.selectbox("Selecciona un servicio:", catalog_options)
    auto_desc = (
        selected_item.replace("⭐ ", "") if selected_item in custom_catalog_map
        else (selected_item if selected_item != "Escribir manualmente..." else "")
    )
    auto_price = float(custom_catalog_map.get(selected_item, 0.0))

    item_desc = st.text_input("Descripción", value=auto_desc)
    col3, col4 = st.columns(2)
    item_qty = col3.number_input("Cant.", min_value=1, value=1)
    item_price = col4.number_input("Precio (HT)", min_value=0.0, value=auto_price)

    if st.button("➕ Agregar"):
        if item_desc and item_price > 0:
            st.session_state.cart.append({
                "desc": item_desc, "qty": item_qty,
                "price": item_price, "total": item_qty * item_price
            })
            st.session_state.pdf_ready = False
        else:
            st.warning("Completa la descripción y el precio.")

    subtotal = 0.0
    if st.session_state.cart:
        st.markdown("#### 📋 Tu Lista:")
        for i, it in enumerate(st.session_state.cart):
            col_c1, col_c2, col_c3 = st.columns([4, 2, 1])
            col_c1.write(f"**{it['qty']}x** {it['desc']}")
            col_c2.write(f"{currency} {it['total']:.2f}")
            if col_c3.button("🗑️", key=f"del_{i}", help="Eliminar este item"):
                st.session_state.cart.pop(i)
                st.session_state.pdf_ready = False
                st.rerun()
            subtotal += it['total']

    st.divider()

    # --- Discount, IVA, Totals ---
    st.markdown("### 💰 Descuentos e Impuestos")
    col_disc1, col_disc2 = st.columns(2)
    discount_type = col_disc1.radio("Tipo de descuento:", ["Sin descuento", "Monto fijo", "Porcentaje (%)"], horizontal=False)
    discount_value = 0.0
    if discount_type == "Monto fijo":
        discount_value = col_disc2.number_input(f"Descuento ({currency})", min_value=0.0, value=0.0)
    elif discount_type == "Porcentaje (%)":
        disc_pct = col_disc2.number_input("Descuento (%)", min_value=0.0, max_value=100.0, value=0.0)
        discount_value = subtotal * disc_pct / 100

    apply_iva = st.checkbox("Aplicar IVA (12%)")

    subtotal_after_discount = subtotal - discount_value
    iva_amount = subtotal_after_discount * IVA_RATE if apply_iva else 0.0
    grand_total = subtotal_after_discount + iva_amount

    st.markdown(f"**Subtotal:** {currency} {subtotal:.2f}")
    if discount_value > 0:
        st.markdown(f"**Descuento:** - {currency} {discount_value:.2f}")
    if iva_amount > 0:
        st.markdown(f"**IVA (12%):** {currency} {iva_amount:.2f}")
    st.markdown(f"#### Total: {currency} {grand_total:.2f}")

    st.divider()

    # --- Advance & Validity ---
    col_adv, col_val = st.columns(2)
    require_advance = col_adv.checkbox("Requerir Anticipo")
    advance_amount, balance_due = 0.0, grand_total
    if require_advance and grand_total > 0:
        advance_pct = st.slider("Porcentaje anticipo (%)", min_value=10, max_value=100, value=50, step=10) / 100.0
        advance_amount = grand_total * advance_pct
        balance_due = grand_total - advance_amount
        st.info(f"**Anticipo:** {currency} {advance_amount:.2f} | **Saldo:** {currency} {balance_due:.2f}")

    validity_days = col_val.number_input("Válida por (días)", min_value=1, value=15)
    validity_date = (datetime.now() + timedelta(days=int(validity_days))).strftime("%d/%m/%Y")

    st.divider()

    if st.button("Preparar Cotización ✨", type="primary", use_container_width=True):
        if not c_name:
            st.error("Por favor ingresa el nombre del cliente.")
        elif not st.session_state.cart:
            st.error("Agrega al menos un producto o servicio.")
        else:
            st.session_state.smart_url = ""

            clean_phone = ''.join(filter(str.isdigit, c_phone)) if c_phone else ""
            if phone_prefix and clean_phone.startswith(phone_prefix):
                clean_phone = clean_phone[len(phone_prefix):]
            display_phone = f"+{phone_prefix} {clean_phone}" if clean_phone and phone_prefix else c_phone
            currency_symbol = "Q" if "Q" in currency else "$"

            # Generate quote number
            now = datetime.now()
            quote_number = ""
            if st.session_state.user:
                quote_number = get_quote_number(
                    st.session_state.user.id, now.year,
                    st.session_state.total_quotes_count
                )

            q_data = {
                "date": now.strftime("%d/%m/%Y"),
                "validity_date": validity_date,
                "quote_number": quote_number,
                "client_name": c_name,
                "client_email": c_email,
                "display_phone": display_phone,
                "client_nit": c_nit,
                "currency": currency_symbol,
                "cart": st.session_state.cart,
                "subtotal": subtotal,
                "discount_amount": discount_value,
                "iva_amount": iva_amount,
                "grand_total": grand_total,
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
                "terms": profile.get('terms_conditions'),
            }

            with st.spinner("✨ Creando tu cotización profesional..."):
                st.session_state.pdf_bytes = generate_pdf(q_data)
                smart_url = ""

                if st.session_state.user:
                    try:
                        db_quote_data = {k: v for k, v in q_data.items() if k != 'logo_file'}
                        _, secure_pdf_url = upload_pdf_to_storage(
                            st.session_state.pdf_bytes, st.session_state.user.id
                        )
                        res = supabase.table("quotes").insert({
                            "user_id": st.session_state.user.id,
                            "client_name": c_name,
                            "total_amount": grand_total,
                            "currency": currency_symbol,
                            "quote_data": db_quote_data,
                            "pdf_url": secure_pdf_url,
                            "status": "enviada",
                            "quote_number": quote_number,
                            "views_count": 0,
                        }).execute()

                        quote_id = res.data[0]['id']
                        smart_url = f"{BASE_URL}/?doc={quote_id}"
                        st.session_state.smart_url = smart_url
                        st.session_state.quotes_this_month += 1
                        st.session_state.total_quotes_count += 1

                        existing_client = next(
                            (c for c in st.session_state.clients if c['name'] == c_name), None
                        )
                        client_payload = {
                            "user_id": st.session_state.user.id,
                            "name": c_name, "phone": c_phone,
                            "email": c_email, "nit": c_nit,
                        }
                        if existing_client:
                            client_payload["id"] = existing_client["id"]
                        supabase.table("clients").upsert(client_payload).execute()
                        fetch_user_data(force=True)

                    except Exception as e:
                        logger.error(f"DB/Storage error: {e}")
                        st.warning("Cotización generada localmente (error al guardar en la nube).")

                if c_phone:
                    full_number = f"{phone_prefix}{clean_phone}"
                    wa_msg = (
                        f"Hola {c_name}.\nTe comparto la cotización de "
                        f"{seller_name or 'nuestro servicio'}.\n"
                        f"El total es de {currency_symbol} {grand_total:.2f}."
                    )
                    if smart_url:
                        wa_msg += f"\n\nVer y descargar tu documento aquí:\n{smart_url}"
                    else:
                        wa_msg += "\n\n(Regístrate en CotiListo para enviar el PDF por enlace directo)."
                    st.session_state.wa_url = (
                        f"https://wa.me/{full_number}?text={urllib.parse.quote(wa_msg.encode('utf-8'))}"
                    )
                else:
                    st.session_state.wa_url = ""

            st.session_state.last_client_name = c_name
            st.session_state.last_client_email = c_email
            st.session_state.pdf_ready = True
            st.session_state.cart = []
            st.balloons()

    if st.session_state.get('pdf_ready'):
        display_name = st.session_state.get('last_client_name', '')
        st.success(f"¡Cotización lista para {display_name}!")

        col_act1, col_act2, col_act3 = st.columns(3)
        with col_act1:
            if st.session_state.get('wa_url'):
                st.link_button("💬 WhatsApp", st.session_state.wa_url, use_container_width=True)
            else:
                st.button("💬 WhatsApp", disabled=True, use_container_width=True)
        with col_act2:
            link_to_send = st.session_state.get('smart_url') or "(Enlace no disponible - adjunta el PDF)"
            email_to = st.session_state.get('last_client_email', '')
            subject = urllib.parse.quote(f"Tu Cotización - {display_name}".encode('utf-8'))
            body = urllib.parse.quote(
                f"Hola {display_name},\n\nAdjunto el enlace a tu cotización segura:\n{link_to_send}\n\nSaludos cordiales,".encode('utf-8')
            )
            st.link_button("📧 Email", f"mailto:{email_to}?subject={subject}&body={body}", use_container_width=True)
        with col_act3:
            safe_name = "".join(ch for ch in display_name if ch.isalnum() or ch in " _-").strip().replace(" ", "_")
            st.download_button(
                label="📥 Descargar",
                data=st.session_state.pdf_bytes,
                file_name=f"Cotizacion_{safe_name}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

        if not st.session_state.user:
            st.divider()
            with st.container(border=True):
                st.warning(
                    "💡 **¿Te gustó?** Con una cuenta gratuita, tu cliente recibe un enlace "
                    "directo en WhatsApp y tú guardas el historial automáticamente."
                )
                col_b1, col_b2 = st.columns(2)
                with col_b1:
                    st.page_link(page_log, label="Crear cuenta gratis →", icon="✨")
                with col_b2:
                    st.page_link(page_log, label="Ingresar a mi cuenta", icon="🔑")


# ==========================================
# 🗂️ PAGE: HISTORY
# ==========================================
def page_history():
    st.title("🗂️ Historial de Cotizaciones")
    st.markdown("Gestiona, reenvía y actualiza tus documentos.")

    if not st.session_state.user:
        st.warning("Por favor, inicia sesión para ver tu historial.")
        return

    try:
        res = supabase.table("quotes").select("*") \
            .eq("user_id", st.session_state.user.id) \
            .order("created_at", desc=True).execute()
        quotes = res.data
    except Exception as e:
        logger.error(f"History fetch error: {e}")
        st.error("Error cargando el historial.")
        return

    if not quotes:
        st.info("Aún no has creado ninguna cotización.")
        st.page_link(page_gen, label="➕ Crear mi primera cotización", icon="📝")
        return

    _purge_expired_pdfs(st.session_state.user.id, quotes)

    # --- Filters ---
    col_search, col_filter = st.columns([2, 1])
    search = col_search.text_input("🔍 Buscar cliente...", placeholder="Nombre del cliente")
    status_filter = col_filter.selectbox("Estado:", ["Todas", "Enviadas", "Ganadas"])

    if search:
        quotes = [q for q in quotes if search.lower() in q.get("client_name", "").lower()]
    if status_filter == "Enviadas":
        quotes = [q for q in quotes if q.get("status", "enviada") == "enviada"]
    elif status_filter == "Ganadas":
        quotes = [q for q in quotes if q.get("status") == "ganada"]

    if not quotes:
        st.info("No se encontraron cotizaciones con esos filtros.")
        return

    for q in quotes:
        q_data = q.get("quote_data", {})
        client_name = q.get("client_name", "Cliente")
        total_amount = q.get("total_amount", 0)
        currency = q.get("currency", "$")
        doc_id = q.get("id")
        status = q.get("status", "enviada")
        views_count = q.get("views_count", 0)
        quote_number = q.get("quote_number", "")
        internal_notes = q.get("internal_notes", "")

        created_date = datetime.fromisoformat(q['created_at'].replace("Z", "+00:00"))
        days_old = (datetime.now(timezone.utc) - created_date).days
        smart_url = f"{BASE_URL}/?doc={doc_id}"

        # Dynamic icon
        if status == "ganada":
            status_icon = "🏆"
        elif days_old > PDF_LINK_EXPIRY_DAYS:
            status_icon = "⚠️"
        else:
            status_icon = "📄"

        num_label = f" {quote_number}" if quote_number else ""
        views_label = f" · 👁️ {views_count}" if views_count > 0 else ""
        expander_label = f"{status_icon}{num_label} {client_name} — {currency} {float(total_amount):.2f} (Hace {days_old} días){views_label}"

        with st.expander(expander_label):
            # Internal notes
            new_notes = st.text_area(
                "📝 Notas internas (privadas)",
                value=internal_notes,
                placeholder="Ej: Cliente muy interesado, llamar el lunes...",
                key=f"notes_{doc_id}",
                height=70
            )
            if new_notes != internal_notes:
                if st.button("💾 Guardar nota", key=f"save_notes_{doc_id}"):
                    try:
                        supabase.table("quotes").update(
                            {"internal_notes": new_notes}
                        ).eq("id", doc_id).execute()
                        st.toast("Nota guardada ✅")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al guardar nota: {e}")

            if days_old > PDF_LINK_EXPIRY_DAYS and status != "ganada":
                st.error("⚠️ Este enlace ha expirado para el cliente (>30 días)")
                if st.button("🔄 Regenerar Cotización", key=f"regen_{doc_id}", use_container_width=True):
                    try:
                        old_pdf_url = q.get("pdf_url", "")
                        if old_pdf_url:
                            try:
                                old_path = old_pdf_url.split("/quotations/")[1].split("?")[0]
                                supabase.storage.from_("quotations").remove([old_path])
                            except Exception as e:
                                logger.warning(f"Could not delete old PDF: {e}")
                        new_pdf_bytes = generate_pdf(q_data)
                        _, new_secure_url = upload_pdf_to_storage(new_pdf_bytes, st.session_state.user.id)
                        supabase.table("quotes").update({
                            "pdf_url": new_secure_url,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        }).eq("id", doc_id).execute()
                        st.success("¡Cotización regenerada!")
                        st.rerun()
                    except Exception as e:
                        logger.error(f"Regen error: {e}")
                        st.error(f"Error al regenerar: {e}")
            else:
                # Build URLs
                client_phone = q_data.get("display_phone", "")
                client_email = q_data.get("client_email", "")
                clean_phone = ''.join(filter(str.isdigit, client_phone))

                wa_send_url = f"https://wa.me/{clean_phone}?text={urllib.parse.quote(('Hola ' + client_name + '. Te comparto el enlace de tu cotización:\n' + smart_url).encode('utf-8'))}" if clean_phone else ""
                wa_followup_url = f"https://wa.me/{clean_phone}?text={urllib.parse.quote(('Hola ' + client_name + ', ¿tuviste la oportunidad de revisar la cotización que te envié? Quedo a tu disposición para cualquier consulta.').encode('utf-8'))}" if clean_phone else ""

                subject = urllib.parse.quote(f"Tu Cotización - {client_name}".encode('utf-8'))
                body = urllib.parse.quote(
                    f"Hola {client_name},\n\nAdjunto el enlace a tu cotización segura:\n{smart_url}\n\nSaludos cordiales,".encode('utf-8')
                )

                # Row 1: Ver, WhatsApp, Email
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.link_button("👁️ Ver", smart_url, use_container_width=True)
                with col2:
                    if wa_send_url:
                        st.link_button("💬 WhatsApp", wa_send_url, use_container_width=True)
                    else:
                        st.button("💬 WhatsApp", disabled=True, use_container_width=True)
                with col3:
                    st.link_button("📧 Email", f"mailto:{client_email}?subject={subject}&body={body}", use_container_width=True)

                # Row 2: Seguimiento, Copiar enlace, Duplicar
                col4, col5, col6 = st.columns(3)
                with col4:
                    if wa_followup_url:
                        st.link_button("🔔 Seguimiento", wa_followup_url, use_container_width=True)
                    else:
                        st.button("🔔 Seguimiento", disabled=True, use_container_width=True)
                with col5:
                    # Copy link button via JS
                    components.html(f"""
                        <button onclick="navigator.clipboard.writeText('{smart_url}').then(() => alert('¡Enlace copiado!'))"
                            style="width:100%;padding:6px;background:#f0f2f6;border:1px solid #d0d3da;
                                   border-radius:4px;cursor:pointer;font-size:14px;">
                            📋 Copiar enlace
                        </button>
                    """, height=40)
                with col6:
                    if st.button("📋 Duplicar", key=f"dup_{doc_id}", use_container_width=True):
                        st.session_state.cart = q_data.get("cart", [])
                        st.session_state.pdf_ready = False
                        st.toast("🛒 Carrito copiado. Por favor, ingresa los datos del nuevo cliente.")
                        st.switch_page(page_gen)

                # Row 3: Ganada, Eliminar
                col7, col8 = st.columns(2)
                with col7:
                    if status != "ganada":
                        if st.button("🏆 Marcar como Ganada", key=f"won_{doc_id}", use_container_width=True):
                            try:
                                supabase.table("quotes").update({"status": "ganada"}).eq("id", doc_id).execute()
                                st.session_state.total_ganado += float(total_amount)
                                st.session_state.quotes_won_count += 1
                                st.balloons()
                                st.toast("¡Felicidades! 🏆 ¡Trato cerrado!", icon="🏆")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error: {e}")
                    else:
                        st.success("🏆 ¡Ganada!")

                with col8:
                    if st.button("🗑️ Eliminar", key=f"del_btn_{doc_id}", use_container_width=True):
                        st.session_state[f"confirm_del_{doc_id}"] = True

                if st.session_state.get(f"confirm_del_{doc_id}"):
                    st.warning("⚠️ ¿Estás seguro? Esta acción no se puede deshacer.")
                    col_yes, col_no = st.columns(2)
                    with col_yes:
                        if st.button("🚨 Sí, borrar", key=f"yes_{doc_id}", use_container_width=True):
                            try:
                                old_pdf_url = q.get("pdf_url", "")
                                if old_pdf_url:
                                    try:
                                        old_path = old_pdf_url.split("/quotations/")[1].split("?")[0]
                                        supabase.storage.from_("quotations").remove([old_path])
                                    except Exception:
                                        pass
                                supabase.table("quotes").delete().eq("id", doc_id).execute()
                                if status == "ganada":
                                    st.session_state.total_ganado = max(0, st.session_state.total_ganado - float(total_amount))
                                    st.session_state.quotes_won_count = max(0, st.session_state.quotes_won_count - 1)
                                st.session_state[f"confirm_del_{doc_id}"] = False
                                st.success("Cotización eliminada.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error al eliminar: {e}")
                    with col_no:
                        if st.button("❌ Cancelar", key=f"no_{doc_id}", use_container_width=True):
                            st.session_state[f"confirm_del_{doc_id}"] = False
                            st.rerun()


# ==========================================
# 👥 PAGE: CLIENTES
# ==========================================
def page_clients():
    st.page_link(page_gen, label="Volver al Generador", icon="⬅️")
    st.title("👥 Mis Clientes")
    if not st.session_state.user:
        st.warning("Inicia sesión para ver tus clientes.")
        return

    fetch_user_data()
    if not st.session_state.clients:
        st.info("No tienes clientes guardados.")
        return

    for c in st.session_state.clients:
        with st.container(border=True):
            with st.expander(f"👤 **{c['name']}**"):
                new_phone = st.text_input("Teléfono", value=c.get('phone', ''), key=f"p_{c['id']}")
                new_email = st.text_input("Email", value=c.get('email', ''), key=f"e_{c['id']}")
                new_nit = st.text_input("NIT / ID Fiscal", value=c.get('nit', ''), key=f"n_{c['id']}")
                if st.button("💾 Guardar", key=f"btn_{c['id']}"):
                    try:
                        supabase.table("clients").update({
                            "phone": new_phone, "email": new_email, "nit": new_nit
                        }).eq("id", c['id']).execute()
                        fetch_user_data(force=True)
                        st.success("¡Actualizado!")
                        st.rerun()
                    except Exception as e:
                        logger.error(f"Client update error: {e}")
                        st.error(f"Error al guardar: {e}")


# ==========================================
# ⚙️ PAGE: PERFIL
# ==========================================
def page_profile():
    if st.session_state.get("profile_saved"):
        st.toast("¡Perfil guardado con éxito!", icon="✅")
        st.session_state.profile_saved = False
    if st.session_state.get("catalog_saved"):
        st.toast("¡Catálogo actualizado!", icon="📚")
        st.session_state.catalog_saved = False

    st.page_link(page_gen, label="Volver al Generador", icon="⬅️")
    st.title("⚙️ Mi Perfil")
    if not st.session_state.user:
        st.warning("Inicia sesión para configurar tu perfil.")
        return

    fetch_user_data()
    profile = st.session_state.user_profile
    catalog = list(profile.get("catalog", []))

    with st.expander("🏢 Negocio, Logo y Banco", expanded=True):
        name = st.text_input("Nombre del Negocio", value=profile.get('business_name', ''))

        # Default currency
        default_curr = profile.get("default_currency", "Q")
        default_curr_sel = st.radio("Moneda por defecto:", ["Q", "$"],
                                    index=0 if default_curr == "Q" else 1, horizontal=True)

        st.markdown("**🖼️ Logo del Negocio**")
        current_logo = profile.get("logo_url", "")
        new_logo = st.file_uploader("Sube tu nuevo logo (PNG, JPG - Máx 2MB)", type=["png", "jpg", "jpeg"])

        if new_logo:
            st.image(new_logo, width=150, caption="✨ Vista previa del nuevo logo")
            st.info("👇 Haz clic en 'Guardar Cambios' abajo para confirmar y subir el logo.")
        elif current_logo:
            st.image(current_logo, width=150, caption="Logo Actual")

        st.divider()

        st.markdown("**🏦 Información de Pago**")
        current_bank = profile.get('bank_name', '')
        bank_index = GUATEMALA_BANKS.index(current_bank) if current_bank in GUATEMALA_BANKS else 0
        bank_name = st.selectbox("Banco", GUATEMALA_BANKS, index=bank_index)
        acc_type = st.radio("Tipo de Cuenta", ["Monetaria", "Ahorro"],
                            index=0 if profile.get('account_type') == "Monetaria" else 1, horizontal=True)
        acc_num = st.text_input("Número de Cuenta", value=profile.get('account_number', ''))
        acc_name = st.text_input("Nombre en la Cuenta", value=profile.get('account_name', ''))

        st.markdown("**📜 Condiciones**")
        st.info(
            "💡 **Ejemplos rápidos:**\n"
            "**Servicios:** *Cotización válida por 15 días. Anticipo del 50% no reembolsable para agendar.*\n"
            "**Productos:** *Precios sujetos a cambios. Garantía de 30 días contra defectos de fábrica.*"
        )
        terms = st.text_area("Condiciones", value=profile.get('terms_conditions', ''))

        if st.button("💾 Guardar Cambios", type="primary"):
            final_logo_url = current_logo

            if new_logo:
                if current_logo:
                    try:
                        old_path = current_logo.split("/logos/")[1]
                        supabase.storage.from_("logos").remove([old_path])
                    except Exception as e:
                        logger.warning(f"Old logo delete failed: {e}")

                file_ext = new_logo.name.split('.')[-1].lower()
                file_path = f"{st.session_state.user.id}/logo_{int(datetime.now().timestamp())}.{file_ext}"
                try:
                    supabase.storage.from_("logos").upload(
                        file=new_logo.getvalue(), path=file_path,
                        file_options={"content-type": f"image/{file_ext}", "upsert": "true"}
                    )
                    final_logo_url = supabase.storage.from_("logos").get_public_url(file_path)
                except Exception as e:
                    logger.error(f"Logo upload error: {e}")
                    st.error(f"Error al subir logo a Storage: {e}")

            try:
                supabase.table("profiles").upsert({
                    "id": st.session_state.user.id,
                    "business_name": name,
                    "default_currency": default_curr_sel,
                    "logo_url": final_logo_url,
                    "bank_name": bank_name,
                    "account_type": acc_type,
                    "account_number": acc_num,
                    "account_name": acc_name,
                    "terms_conditions": terms,
                    "catalog": catalog,
                }).execute()
                fetch_user_data(force=True)
                st.session_state.profile_saved = True
                st.rerun()
            except Exception as e:
                logger.error(f"Profile save error: {e}")
                st.error(f"Error de base de datos: {e}")

    st.subheader("📚 Mi Catálogo")
    if catalog:
        for idx, item in enumerate(catalog):
            col1, col2, col3 = st.columns([4, 2, 1])
            col1.write(f"🔹 {item['desc']}")
            col2.write(f"{profile.get('default_currency', 'Q')} {item['price']}")
            if col3.button("❌", key=f"del_cat_{idx}"):
                catalog.pop(idx)
                try:
                    supabase.table("profiles").upsert({**profile, "catalog": catalog}).execute()
                    fetch_user_data(force=True)
                    st.session_state.catalog_saved = True
                    st.rerun()
                except Exception as e:
                    logger.error(f"Catalog delete error: {e}")
                    st.error(f"Error al eliminar: {e}")
    else:
        st.info("Catálogo vacío.")

    with st.container(border=True):
        new_desc = st.text_input("Servicio / Producto")
        new_price = st.number_input("Precio", min_value=0.0)
        if st.button("➕ Guardar en Catálogo"):
            if new_desc and new_price > 0:
                catalog.append({"desc": new_desc, "price": new_price})
                try:
                    supabase.table("profiles").upsert({**profile, "catalog": catalog}).execute()
                    fetch_user_data(force=True)
                    st.session_state.catalog_saved = True
                    st.rerun()
                except Exception as e:
                    logger.error(f"Catalog add error: {e}")
                    st.error(f"Error al añadir: {e}")
            else:
                st.warning("Completa la descripción y el precio.")

    st.subheader("🔒 Seguridad")
    with st.expander("Cambiar mi Contraseña"):
        new_password = st.text_input("Nueva Contraseña", type="password")
        if st.button("Actualizar Contraseña"):
            if len(new_password) >= 6:
                try:
                    supabase.auth.update_user({"password": new_password})
                    st.success("✅ ¡Contraseña actualizada con éxito!")
                except Exception as e:
                    logger.error(f"Password update error: {e}")
                    st.error(f"Error al actualizar la contraseña: {e}")
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
        wa_message = urllib.parse.quote("Hola Romain. Necesito ayuda o tengo un comentario sobre CotiListo: ")
        st.info("💡 Tu feedback es vital para seguir haciendo crecer esta plataforma.")
        st.link_button("Contactar por WhatsApp 🟢", f"https://wa.me/{wa_number}?text={wa_message}", use_container_width=True)


# ==========================================
# 🔐 PAGE: LOGIN
# ==========================================
def process_login():
    try:
        res = supabase.auth.sign_in_with_password({
            "email": st.session_state.login_email.strip(),
            "password": st.session_state.login_pw,
        })
        st.session_state.user = res.user
        # Save persistent session cookie
        if res.session:
            save_session_cookie(res.session.access_token, res.session.refresh_token)
        fetch_user_data(force=True)
        st.session_state.show_welcome = True
        st.session_state.login_error = None
    except Exception as e:
        logger.warning(f"Login failed: {e}")
        st.session_state.login_error = "Credenciales incorrectas. Verifica tu email y contraseña."


def process_registration():
    try:
        supabase.auth.sign_up({
            "email": st.session_state.reg_email.strip(),
            "password": st.session_state.reg_pw,
        })
        st.session_state.reg_msg = "✅ Cuenta creada. Revisa tu correo para confirmar tu cuenta antes de ingresar."
        st.session_state.reg_error = None
    except Exception as e:
        logger.warning(f"Registration failed: {e}")
        st.session_state.reg_error = f"Error al registrarse: {e}"


def page_login():
    st.page_link(page_gen, label="Volver al Generador", icon="⬅️")
    st.title("🔐 Acceso Premium")
    if not supabase:
        st.error("Error de configuración del servidor.")
        return

    if st.session_state.get("password_changed_success"):
        st.success("✅ Contraseña actualizada. Ya puedes ingresar.")
        st.session_state.password_changed_success = False

    tab1, tab2 = st.tabs(["Ingresar", "Crear Cuenta"])

    with tab1:
        with st.form("login_form"):
            st.text_input("Email", key="login_email", autocomplete="email")
            st.text_input("Contraseña", type="password", key="login_pw", autocomplete="current-password")
            st.form_submit_button("Entrar", type="primary", use_container_width=True, on_click=process_login)

        if st.session_state.get("login_error"):
            st.error(st.session_state.login_error)
            st.session_state.login_error = None

        st.write("")
        with st.expander("¿Olvidaste tu contraseña?"):
            if not st.session_state.get("recovery_code_sent", False):
                st.markdown("<small>Ingresa tu email para recibir un código de recuperación.</small>", unsafe_allow_html=True)
                reset_email = st.text_input("Tu Email", key="reset_email_input",
                                            label_visibility="collapsed", placeholder="ejemplo@correo.com")
                if st.button("Enviar código", use_container_width=True):
                    if reset_email:
                        try:
                            supabase.auth.reset_password_email(reset_email.strip())
                            st.session_state.recovery_email = reset_email.strip()
                            st.session_state.recovery_code_sent = True
                            st.rerun()
                        except Exception as e:
                            logger.error(f"Password reset error: {e}")
                            st.error("Error al enviar el código. Verifica el email.")
                    else:
                        st.warning("Por favor, ingresa tu email.")
            else:
                st.success(f"📧 Código enviado a **{st.session_state.recovery_email}**")
                recovery_code = st.text_input("Código de recuperación")
                new_pw = st.text_input("Nueva Contraseña", type="password")
                col_btn1, col_btn2 = st.columns(2)

                if col_btn1.button("Actualizar", type="primary", use_container_width=True):
                    if recovery_code and len(new_pw) >= 6:
                        try:
                            supabase.auth.verify_otp({
                                "email": st.session_state.recovery_email,
                                "token": recovery_code.strip(),
                                "type": "recovery",
                            })
                            supabase.auth.update_user({"password": new_pw})
                            supabase.auth.sign_out()
                            st.session_state.password_changed_success = True
                            st.session_state.recovery_code_sent = False
                            st.rerun()
                        except Exception as e:
                            logger.warning(f"OTP verify failed: {e}")
                            st.error("El código es incorrecto o ha expirado.")
                    else:
                        st.warning("Completa los campos (mínimo 6 caracteres).")

                if col_btn2.button("Cancelar", use_container_width=True):
                    st.session_state.recovery_code_sent = False
                    st.rerun()

    with tab2:
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

        with st.expander("📲 Instalar CotiListo como App"):
            st.markdown("""
            Accede más rápido desde tu teléfono:

            **En Android (Chrome):**
            1. Toca los **⋮** (arriba a la derecha).
            2. Selecciona **"Instalar aplicación"** o **"Agregar a la pantalla principal"**.

            **En iPhone (Safari):**
            1. Toca el ícono de **Compartir** (cuadrado con flecha ↑).
            2. Desliza hacia abajo y selecciona **"Agregar a inicio"**.

            ---
            """)
            st.image("tutorial_android.gif", use_container_width=True)

        if st.button("🚪 Cerrar Sesión", use_container_width=True):
            delete_session_cookie()
            supabase.auth.sign_out()
            st.session_state.user = None
            st.session_state.user_profile = {}
            st.session_state.clients = []
            st.session_state.user_data_loaded = False
            st.session_state.quotes_this_month = 0
            st.session_state.total_ganado = 0.0
            st.session_state.quotes_won_count = 0
            st.session_state.total_quotes_count = 0
            st.session_state.last_client_name = ""
            st.session_state.last_client_email = ""
            st.session_state.session_restored = False
            st.rerun()
    pg = st.navigation([page_gen, page_hist, page_crm, page_prof, page_sup])
else:
    pg = st.navigation([page_gen, page_sup, page_log])

pg.run()