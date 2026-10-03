import os
import hashlib
import gzip
import bz2
import zipfile
import io
import json
import uuid
from flask import Flask, request, send_file, render_template, jsonify, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__)

BASE_DIR = os.path.dirname(__file__)
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')
SESSION_FILE = os.path.join(BASE_DIR, 'session.json')

os.makedirs(UPLOADS_DIR, exist_ok=True)

REPO_INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{repo_name}</title>
    <link rel="icon" type="image/png" href="CydiaIcon.png">
    <link rel="apple-touch-icon" href="CydiaIcon.png">
    <style>
        body {{ font-family: -apple-system, sans-serif; background: #cbd2d8; margin: 0; padding: 12px; display: flex; justify-content: center; }}
        .container {{ width: 100%; max-width: 800px; }}
        .card {{ background: white; border-radius: 12px; padding: 15px; margin-bottom: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); display: flex; align-items: center; gap: 15px; }}
        .icon {{ width: 64px; height: 64px; border-radius: 14px; object-fit: cover; flex-shrink: 0; background: #f0f0f0; }}
        .title {{ font-size: 22px; font-weight: bold; margin: 0; }}
        .desc {{ color: #666; font-size: 13px; margin-top: 3px; }}
        .btn-group {{ display: flex; gap: 8px; margin-bottom: 12px; }}
        .btn {{ flex: 1; background: #007aff; color: white; text-align: center; padding: 10px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 13px; }}
        .tweak-list {{ background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .tweak-item {{ padding: 12px 15px; border-bottom: 1px solid #eee; display: flex; align-items: center; gap: 12px; }}
        .tweak-item:last-child {{ border-bottom: none; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <img src="CydiaIcon.png" class="icon" onerror="this.style.display='none'">
            <div>
                <h1 class="title">{repo_name}</h1>
                <div class="desc">{repo_desc}</div>
            </div>
        </div>
        <div class="btn-group">
            <a href="sileo://source/https://{clean_repo_url}" class="btn">Sileo</a>
            <a href="zbra://sources/add/https://{clean_repo_url}" class="btn">Zebra</a>
            <a href="cydia://url/https://cydia.saurik.com/api/share#?source=https://{clean_repo_url}" class="btn">Cydia</a>
        </div>
        
        <h3 style="color: #4d566f; margin: 10px 0 6px 6px; font-size: 15px;">Пакеты</h3>
        <div class="tweak-list">
            {tweaks_html}
        </div>
    </div>
</body>
</html>"""

def hash_data(data):
    return {
        'size': str(len(data)),
        'md5': hashlib.md5(data).hexdigest(),
        'sha1': hashlib.sha1(data).hexdigest(),
        'sha256': hashlib.sha256(data).hexdigest()
    }

def clean_url(url):
    url = url.strip()
    if url.startswith('https://'): url = url[8:]
    if url.startswith('http://'): url = url[7:]
    return url.rstrip('/')

# Умная функция для поиска файлов: извлекает только имя файла и ищет в текущей папке uploads,
# предотвращая баги с потерей файлов при перезапуске сервера (если сменился абсолютный путь)
def get_valid_upload_path(saved_path):
    if not saved_path: return None
    filename = os.path.basename(saved_path)
    current_path = os.path.join(UPLOADS_DIR, filename)
    if os.path.exists(current_path):
        return current_path
    return None

@app.route('/')
def index():
    return render_template('builder.html')

@app.route('/get_session', methods=['GET'])
def get_session():
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
        except Exception:
            pass
    return jsonify({
        'repo': {'name': '', 'url': '', 'desc': '', 'icon_saved_path': '', 'icon_preview': ''},
        'tweaks': []
    })

@app.route('/save_session', methods=['POST'])
def save_session():
    data = request.get_json()
    with open(SESSION_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({'status': 'ok'})

@app.route('/reset_session', methods=['POST'])
def reset_session():
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
    return jsonify({'status': 'ok'})

@app.route('/upload_draft', methods=['POST'])
def upload_draft():
    if 'file' not in request.files:
        return jsonify({'error': 'Файл не найден'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Пустое имя файла'}), 400
    
    unique_filename = f"{uuid.uuid4().hex[:8]}_{secure_filename(file.filename)}"
    save_path = os.path.join(UPLOADS_DIR, unique_filename)
    file.save(save_path)
    
    return jsonify({
        'saved_path': save_path,
        'filename': unique_filename,
        'original_name': file.filename,
        'preview_url': f"/uploads/{unique_filename}"
    })

@app.route('/uploads/<filename>')
def serve_upload(filename):
    return send_from_directory(UPLOADS_DIR, filename)

@app.route('/build', methods=['POST'])
def build():
    meta = json.loads(request.form.get('metadata'))
    repo_info = meta['repo']
    tweaks_info = meta['tweaks']
    
    clean_repo_domain = clean_url(repo_info['url'])
    base_full_url = f"https://{clean_repo_domain}"
    
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        
        # 1. Иконка репозитория (корень)
        repo_icon_full_url = ""
        repo_icon_path = get_valid_upload_path(repo_info.get('icon_saved_path'))
        
        if repo_icon_path:
            with open(repo_icon_path, 'rb') as f:
                zf.writestr("CydiaIcon.png", f.read())
            repo_icon_full_url = f"{base_full_url}/CydiaIcon.png"

        packages_text = ""
        sileo_featured_banners = []
        tweaks_html = ""
        
        # 2. Обработка твиков
        for idx, tweak in enumerate(tweaks_info):
            deb_path = get_valid_upload_path(tweak.get('deb_saved_path'))
            if not deb_path: continue
            
            with open(deb_path, 'rb') as f:
                deb_bytes = f.read()
            hashes = hash_data(deb_bytes)
            
            deb_filename_in_zip = f"debs/{tweak['deb_original_name']}"
            zf.writestr(deb_filename_in_zip, deb_bytes)
            
            # Иконка твика
            tweak_icon_url = ""
            tweak_icon_relative = ""
            icon_path = get_valid_upload_path(tweak.get('icon_saved_path'))
            if icon_path:
                ext = os.path.splitext(icon_path)[1]
                icon_zip_name = f"{tweak['package']}{ext}"
                with open(icon_path, 'rb') as f:
                    zf.writestr(f"icons/{icon_zip_name}", f.read())
                tweak_icon_url = f"{base_full_url}/icons/{icon_zip_name}"
                tweak_icon_relative = f"icons/{icon_zip_name}"

            # Баннер твика для Sileo
            tweak_banner_url = ""
            banner_path = get_valid_upload_path(tweak.get('banner_saved_path'))
            if banner_path:
                ext = os.path.splitext(banner_path)[1]
                banner_zip_name = f"{tweak['package']}_banner{ext}"
                with open(banner_path, 'rb') as f:
                    zf.writestr(f"banners/{banner_zip_name}", f.read())
                tweak_banner_url = f"{base_full_url}/banners/{banner_zip_name}"

            # Запись в Packages
            packages_text += f"Package: {tweak['package']}\n"
            packages_text += f"Name: {tweak['name']}\n"
            packages_text += f"Version: {tweak['version']}\n"
            packages_text += f"Architecture: {tweak['architecture']}\n"
            if tweak.get('depends'): packages_text += f"Depends: {tweak['depends']}\n"
            packages_text += f"Filename: {deb_filename_in_zip}\n"
            packages_text += f"Size: {hashes['size']}\n"
            packages_text += f"MD5sum: {hashes['md5']}\n"
            packages_text += f"SHA1: {hashes['sha1']}\n"
            packages_text += f"SHA256: {hashes['sha256']}\n"
            packages_text += f"Section: {tweak['section']}\n"
            packages_text += f"Author: {tweak['author']}\n"
            packages_text += f"Maintainer: {tweak['author']}\n"
            if tweak_icon_url: packages_text += f"Icon: {tweak_icon_url}\n"
            packages_text += f"Depiction: {base_full_url}/index.html#{tweak['package']}-page\n"
            packages_text += f"Description: {tweak['description']}\n\n"
            
            # Массив Featured (только если загружен баннер)
            if tweak.get('featured') and tweak_banner_url:
                sileo_featured_banners.append({
                    "title": tweak['name'],
                    "package": tweak['package'],
                    "url": tweak_banner_url
                })
            
            # HTML
            icon_img_tag = f"<img src='{tweak_icon_relative}' class='icon'>" if tweak_icon_relative else "<div class='icon'></div>"
            tweaks_html += f"""
            <div class="tweak-item">
                {icon_img_tag}
                <div>
                    <div style="font-weight:bold; font-size:15px;">{tweak['name']} <span style="color:#888; font-size:12px;">v{tweak['version']}</span></div>
                    <div style="color:#666; font-size:13px; margin-top:2px;">{tweak['description']}</div>
                </div>
            </div>"""

        pkg_bytes = packages_text.encode('utf-8')
        zf.writestr('Packages', pkg_bytes)
        zf.writestr('Packages.gz', gzip.compress(pkg_bytes))
        zf.writestr('Packages.bz2', bz2.compress(pkg_bytes))
        
        h_raw = hash_data(pkg_bytes)
        h_gz = hash_data(gzip.compress(pkg_bytes))
        h_bz2 = hash_data(bz2.compress(pkg_bytes))
        
        # Динамически добавляем ссылку на json только если есть баннеры
        sileo_featured_line = "SileoFeatured: sileo-featured.json\n" if sileo_featured_banners else ""
        
        release_text = f"""Origin: {repo_info['name']}
Label: {repo_info['name']}
Suite: stable
Version: 1.0
Codename: ios
Architectures: iphoneos-arm iphoneos-arm64
Components: main
Icon: {repo_icon_full_url}
{sileo_featured_line}Description: {repo_info['desc']}
MD5Sum:
 {h_raw['md5']} {h_raw['size']} Packages
 {h_gz['md5']} {h_gz['size']} Packages.gz
 {h_bz2['md5']} {h_bz2['size']} Packages.bz2
SHA1:
 {h_raw['sha1']} {h_raw['size']} Packages
 {h_gz['sha1']} {h_gz['size']} Packages.gz
 {h_bz2['sha1']} {h_bz2['size']} Packages.bz2
SHA256:
 {h_raw['sha256']} {h_raw['size']} Packages
 {h_gz['sha256']} {h_gz['size']} Packages.gz
 {h_bz2['sha256']} {h_bz2['size']} Packages.bz2
"""
        zf.writestr('Release', release_text.encode('utf-8'))
        
        # 3. ИСПРАВЛЕННЫЙ sileo-featured.json (Объект, класс FeaturedBannersView, ключ url)
        if sileo_featured_banners:
            sileo_json = {
                "class": "FeaturedBannersView",
                "itemSize": "{263, 148}",
                "itemCornerRadius": 10,
                "banners": sileo_featured_banners
            }
            zf.writestr('sileo-featured.json', json.dumps(sileo_json, indent=4, ensure_ascii=False).encode('utf-8'))
        
        index_html = REPO_INDEX_TEMPLATE.format(
            repo_name=repo_info['name'],
            repo_desc=repo_info['desc'],
            clean_repo_url=clean_repo_domain,
            tweaks_html=tweaks_html
        )
        zf.writestr('index.html', index_html.encode('utf-8'))

    memory_file.seek(0)
    return send_file(memory_file, download_name='my_compiled_repo.zip', as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, port=5000)