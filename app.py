import sys
import os
import json
import time
import requests
import threading
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from datetime import timedelta, datetime, timezone
from collections import defaultdict
from urllib.parse import urlparse
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.urandom(24)  # For session management
app.permanent_session_lifetime = timedelta(days=7)

@app.context_processor
def inject_version():
    return dict(version=os.environ.get('VERSION', 'v1.3.0'))

# === Configuration Paths ===
CONFIG_DIR = "/config"
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")
# Increment cache version to force re-fetch
CACHE_FILE = os.path.join(CONFIG_DIR, "library_cache_v20.json")

# Ensure config directory exists
os.makedirs(CONFIG_DIR, exist_ok=True)

# Default Theme changed to 'dark'
DEFAULT_THEME = os.environ.get('THEME', 'dark').lower()

# Global Sync Tracker
IS_SYNCING = False

# === Helper Functions ===
def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            # Ensure theme exists in config, default to Env Var if missing
            if 'theme' not in config:
                config['theme'] = DEFAULT_THEME
            return config
    except Exception:
        return None

def save_config(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, 'r') as f:
            data = json.load(f)
            return data.get('items', [])
    except Exception:
        return None

def get_last_sync_time():
    """Reads just the timestamp from the cache file."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, 'r') as f:
            data = json.load(f)
            return data.get('timestamp')
    except Exception:
        return None

def save_cache(items):
    temp_file = CACHE_FILE + ".tmp"
    try:
        # Write to a temp file first
        with open(temp_file, 'w') as f:
            json.dump({'timestamp': time.time(), 'items': items}, f)
            f.flush()
            os.fsync(f.fileno())

        # Overwrite live file with temp file
        os.replace(temp_file, CACHE_FILE)
        print("✅ Cache safely swapped and saved!")
    except Exception as e:
        print(f"❌ Failed to save cache: {e}")
        import traceback
        traceback.print_exc()
        if os.path.exists(temp_file):
            os.remove(temp_file)

def get_jellyfin_headers(api_key):
    return {
        "X-Emby-Token": api_key,
        "Content-Type": "application/json"
    }

# Check if user is on local network, accounting for reverse proxies like NGINX or Caddy
def is_local_request():

    # 1. Get real IP
    if request.headers.getlist("X-Forwarded-For"):
        # Proxies list IPs as: "Real_IP, Proxy1, Proxy2"
        # Want the "Real_IP"
        remote_addr = request.headers.getlist("X-Forwarded-For")[0].split(',')[0].strip()
    else:
        remote_addr = request.remote_addr

    if not remote_addr: return False
    
    # 2. Check Local Ranges
    if remote_addr == '127.0.0.1': return True
    if remote_addr.startswith('10.'): return True
    if remote_addr.startswith('192.168.'): return True
    if remote_addr.startswith('172.'):
        try:
            second_octet = int(remote_addr.split('.')[1])
            if 16 <= second_octet <= 31: return True
        except:
            pass
    return False

# === Jellyfin API Interactions ===
def validate_connection(url, api_key):
    # Helper to clean and try a specific URL
    def try_connect(target_url):
        try:
            target_url = target_url.rstrip('/')
            # Use a short timeout (3s) since we might try multiple addresses
            r = requests.get(f"{target_url}/System/Info", headers=get_jellyfin_headers(api_key), timeout=3)
            if r.status_code == 200:
                return True, target_url
        except Exception:
            pass
        return False, None

    user_input = url.strip()
    candidates = []

    # 1. If user provided a scheme (http:// or https://)
    if '://' in user_input:
        candidates.append(user_input) # Try exact input first
        
        try:
            parsed = urlparse(user_input)
            # If no port is specified in the URL, try appending the default Jellyfin ports
            if parsed.port is None:
                if parsed.scheme == 'http':
                    candidates.append(f"{user_input}:8096")
                elif parsed.scheme == 'https':
                    candidates.append(f"{user_input}:8920")
        except Exception:
            pass # If parsing fails, just rely on the exact input

    # 2. If no scheme provided, generate permutations
    else:
        # Prioritize standard Jellyfin defaults
        candidates.append(f"http://{user_input}:8096")
        candidates.append(f"https://{user_input}:8920")
        # Then try standard ports (80/443) which handles reverse proxies
        candidates.append(f"http://{user_input}")
        candidates.append(f"https://{user_input}")

    # 3. Iterate through candidates until one works
    for candidate in candidates:
        success, valid_url = try_connect(candidate)
        if success:
            return True, valid_url

    return False, None

def get_users(url, api_key):
    try:
        r = requests.get(f"{url}/Users", headers=get_jellyfin_headers(api_key))
        return r.json()
    except Exception:
        return []

def get_user_libraries(url, api_key, user_id):
    """Fetches the 'Views' (Libraries) for a user."""
    try:
        r = requests.get(f"{url}/Users/{user_id}/Views", headers=get_jellyfin_headers(api_key))
        if r.status_code == 200:
            return r.json().get('Items', [])
    except Exception as e:
        print(f"Error fetching views: {e}")
    return []

def get_collection_map(url, headers, user_id):
    """
    Fetches all BoxSets (Collections) and maps item IDs to Collection Names.
    Returns: dict { item_id: [collection_name, ...] }
    """
    app.logger.info(f"!!! DEBUG: Starting collection map build for user: {user_id}")
    collection_map = defaultdict(list)
    
    # 1. Fetch all BoxSets available to the user (Global Search - Reliable)
    params = {
        "IncludeItemTypes": "BoxSet",
        "Recursive": "true",
        "SortBy": "SortName",
        "UserId": user_id,
        "GroupItemsIntoCollections": "false",
        "Limit": 10000 # Added safety limit
    }
    
    try:
        r = requests.get(f"{url}/Users/{user_id}/Items", headers=headers, params=params)
        app.logger.info(f"!!! DEBUG: Collection List Status: {r.status_code}")

        if r.status_code != 200:
            boxsets = r.json().get("Items", [])
            app.logger.info(f"!!! DEBUG: Found {len(boxsets)} Collections in Jellyfin.")
            
        boxsets = r.json().get("Items", [])
        print(f"DEBUG: Found {len(boxsets)} Collections in Jellyfin")
        
        # 2. For each BoxSet, get its child items
        for boxset in boxsets:
            boxset_name = boxset['Name']
            boxset_id = boxset['Id']
            
            # Fetch items inside this specific collection
            c_params = {"ParentId": boxset_id, "UserId": user_id, "Fields": "Id", "Recursive": "true"}
            child_r = requests.get(f"{url}/Users/{user_id}/Items", headers=headers, params=c_params)
            
            if child_r.status_code == 200:
                children = child_r.json().get("Items", [])
                app.logger.info(f"!!! DEBUG: Collection '{boxset_name}' ({boxset_id}) has {len(children)} items.")
                for child in children:
                    collection_map[child['Id']].append(boxset_name)

    except Exception as e:
        app.logger.error(f"!!! DEBUG ERROR: {e}")
    
    sys.stdout.flush() # Force Docker to show the logs
    return collection_map

def fetch_library(force_refresh=False, delta_sync=False):
    config = load_config()
    if not config:
        return []

    if not force_refresh:
        cached = load_cache()
        if cached is not None:
            return cached

        # If cache file doesn't exist, make sure worker is running and return empty grid until synced
        global IS_SYNCING
        if not IS_SYNCING:
            IS_SYNCING = True
            threading.Thread(target=background_sync_worker, kwargs={"force_refresh": True}).start()

        return []
    
    min_date = None
    if delta_sync:
        last_sync = get_last_sync_time()
        if last_sync:
            # Jellyfin expects strict ISO 8601 format (e.g., 2023-10-25T12:00:00.000Z)
            min_date = datetime.fromtimestamp(last_sync, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            print(f"🔄 Delta Sync triggered! Looking for items modified after: {min_date}", flush=True)

    url = config['jellyfin_url']
    headers = get_jellyfin_headers(config['api_key'])
    user_id = config.get('user_id')

    if not user_id:
        return []

   # 1. Fetch Collection Mapping (Pre-fetch)
    collection_map = defaultdict(list)
    if not delta_sync:
        # Full Rebuild: Scrape the API
        collection_map = get_collection_map(url, headers, user_id)
    else:
        # Delta Sync: Rebuild from local cache in 0.01 seconds!
        cached_data = load_cache()
        if cached_data:
            for item in cached_data:
                item_id = item.get('Id')
                if not item_id:
                    continue

                for col in item.get('Collections') or []:
                    if col not in collection_map[item_id]:
                        collection_map[item_id].append(col)

    # 2. Fetch all libraries (Views) for this user
    libraries = get_user_libraries(url, config['api_key'], user_id)
    
    all_items = []
    
    # 3. Iterate through each library and fetch its items
    for lib in libraries:
        lib_name = lib['Name']
        lib_id = lib['Id']
        
        params = {
            "SortBy": "SortName",
            "SortOrder": "Ascending",
            "IncludeItemTypes": "Movie,Series,Video",
            "Recursive": "true",
            "CollapseBoxSetItems": "false",
            "Fields": "PrimaryImageAspectRatio,SeriesName,SeasonNumber,IndexNumber,Genres,ProductionYear,Overview,CommunityRating,OfficialRating,RunTimeTicks,ProviderIds,RecursiveItemCount,ChildCount,BackdropImageTags,DateCreated,Collections,People",
            "ParentId": lib_id,
            "UserID": user_id
        }

        if min_date:
            params["MinDateLastSaved"] = min_date
        
        try:
            r = requests.get(f"{url}/Users/{user_id}/Items", headers=headers, params=params)
            print(f"DEBUG: Jellyfin returned status {r.status_code}")
            
            if r.status_code == 200:
                items = r.json().get("Items", [])

                def process_people(i):
                    people = i.get('People', [])
                    creators = [p.get('Name') for p in people if p.get('Type') == 'Creator' and p.get('Name')]
                    directors = [p.get('Name') for p in people if p.get('Type') == 'Director' and p.get('Name')]
                    actors = [p.get('Name') for p in people if p.get('Type') == 'Actor' and p.get('Name')]
                    
                    i['ExtractedDirectors'] = ", ".join(creators + directors)
                    i['ExtractedActors'] = ", ".join(actors)
                    
                    i.pop('People', None) # Delete the array to keep the cache file tiny!
                
                for item in items:
                    item['LibraryName'] = lib_name
                    item['Collections'] = collection_map.get(item.get('Id')) or []
                    process_people(item)
                    all_items.append(item)
                        
        except Exception as e:
            print(f"Error fetching library '{lib_name}': {e}")
            continue

    # 1. Group our newly fetched Delta items
    delta_updates = {}
    for item in all_items:
        item_id = item['Id']
        if item_id in delta_updates:
            existing_lib = delta_updates[item_id].get('LibraryName', '')
            new_lib = item['LibraryName']
            if new_lib not in existing_lib:
                delta_updates[item_id]['LibraryName'] = f"{existing_lib}, {new_lib}"
        else:
            delta_updates[item_id] = item

    # 2. Pre-load the existing cache if we are doing a Delta Sync
    merged_items = {}
    if delta_sync:
        cached_data = load_cache()
        if cached_data:
            for item in cached_data:
                merged_items[item['Id']] = item

    # 3. Overwrite the old cache with the updated/new items
    for item_id, updated_item in delta_updates.items():
        merged_items[item_id] = updated_item
            
    final_items = list(merged_items.values())
    final_items.sort(key=lambda x: (x.get('Name') or '').lower())

    save_cache(final_items)
    return final_items

# === Routes ===

def background_sync_worker(force_refresh=True, delta_sync=False):
    """The Detached Worker that runs invisibly in the background."""
    global IS_SYNCING
    try:
        # We must use app_context so Flask's logger and tools work inside the thread
        with app.app_context():
            print("⏳ Background sync started...", flush=True)
            fetch_library(force_refresh=force_refresh, delta_sync=delta_sync)
    except Exception as e:
        print(f"❌ Background sync failed: {e}", flush=True)
        import traceback
        traceback.print_exc()
    finally:
        IS_SYNCING = False
        print("🏁 Background worker finished and released lock.", flush=True)

@app.route('/api/sync_status')
def sync_status():
    """API Endpoint for the frontend to poll the sync status."""
    global IS_SYNCING
    return jsonify({"is_syncing": IS_SYNCING})

@app.route('/proxy_image')
def proxy_image():
    config = load_config()
    if not config: return "No Config", 404
    
    # Get the partial path from the request (e.g., /Items/ID/Images/Primary)
    image_path = request.args.get('path')
    if not image_path: return "Missing Path", 400
    
    # Reconstruct the full Jellyfin URL internally
    # We pass along the query string (quality, fillWidth, etc.)
    target_url = f"{config['jellyfin_url']}{image_path}"
    
    try:
        # Fetch the image from Jellyfin using the internal IP
        resp = requests.get(target_url, headers=get_jellyfin_headers(config['api_key']), params=request.args, stream=True)
        return (resp.content, resp.status_code, resp.headers.items())
    except Exception as e:
        return str(e), 500

# Lazy Load People, MediaSources, Path
@app.route('/api/item/<item_id>')
def get_item_details(item_id):
    config = load_config()
    if not config:
        return {"error": "No config"}, 400
    
    url = config['jellyfin_url']
    headers = get_jellyfin_headers(config['api_key'])
    user_id = config.get('user_id')

    # Lazy load time-suckers
    params = {"Fields": "People,MediaSources,Path"}

    try:
        r = requests.get(f"{url}/Users/{user_id}/Items/{item_id}", headers=headers, params=params)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass

    return {"error": "Failed to fetch details"}, 500

@app.route('/')
def index():
    config = load_config()
    if not config:
        return redirect(url_for('setup'))
    
    # Auth Check
    is_admin = session.get('is_admin', False)
    
    # Check if we need to load users for the settings dropdown (only if admin)
    users = []
    if is_admin:
        users = get_users(config['jellyfin_url'], config['api_key'])

    # Library Access Logic
    require_login = config.get('require_login_for_library', False)
    bypass_local = config.get('bypass_local', True)
    
    if require_login and not is_admin:
        if bypass_local and is_local_request():
            pass # Allow access
        else:
            return render_template('index.html', locked=True, config=config)

    query = request.args.get('q', '').lower()
    items = fetch_library()
    
    movies = []
    series = []
    
    # Collections for filters
    all_genres = set()
    all_libraries = set()
    all_collections = set()

    for item in items:
        if query and query not in item.get('Name', '').lower():
            continue
        
        # Primary Image - Optimized for Grid
        item['PosterUrl'] = url_for('proxy_image', path=f"/Items/{item['Id']}/Images/Primary", fillWidth=320, quality=90)
        
        # Backdrop Image - Optimized for Web
        if item.get('BackdropImageTags'):
            item['BackdropUrl'] = url_for('proxy_image', path=f"/Items/{item['Id']}/Images/Backdrop/0", maxWidth=1280, quality=80)
        else:
            item['BackdropUrl'] = ""

        # Default if somehow missing
        if 'LibraryName' not in item:
            item['LibraryName'] = "Unknown Library"

        # Collect metadata for filters
        if item.get('Genres'):
            for g in item['Genres']:
                all_genres.add(g)
        
        if item.get('Collections'):
            for c in item['Collections']:
                all_collections.add(c)
        
        # Split merged library names if comma separated
        libs = item['LibraryName'].split(', ')
        for l in libs:
            all_libraries.add(l)

        if item.get('Type') in ['Movie', 'Video']:
            movies.append(item)
        elif item.get('Type') == 'Series':
            series.append(item)

    # Sort filter lists
    sorted_genres = sorted(list(all_genres))
    sorted_libraries = sorted(list(all_libraries))
    sorted_collections = sorted(list(all_collections))

    return render_template('index.html', 
                         movies=movies, 
                         series=series, 
                         query=query, 
                         config=config, 
                         is_admin=is_admin,
                         users=users,
                         all_genres=sorted_genres,
                         all_libraries=sorted_libraries,
                         all_collections=sorted_collections)

@app.route('/login', methods=['POST'])
def login():
    config = load_config()
    input_password = request.form.get('password')
    stored_hash = config.get('admin_password')

    if stored_hash and check_password_hash(stored_hash, input_password):
        session.permanent = True
        session['is_admin'] = True
        flash('Logged in successfully.')
        return redirect(url_for('index', open_settings='true'))
    else:
        flash('Incorrect password.')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('is_admin', None)
    return redirect(url_for('index'))

@app.route('/update_settings', methods=['POST'])
def update_settings():
    if not session.get('is_admin'):
        return redirect(url_for('index'))
        
    config = load_config()
    
    # Track if we need to re-fetch the library
    needs_refresh = False
    
    # Check for core connectivity changes
    new_url = request.form.get('jellyfin_url')
    new_key = request.form.get('api_key')
    new_user = request.form.get('user_id')
    
    if new_url != config.get('jellyfin_url') or \
       new_key != config.get('api_key') or \
       new_user != config.get('user_id'):
        needs_refresh = True

    # Update standard settings
    config['jellyfin_url'] = new_url
    config['api_key'] = new_key
    config['user_id'] = new_user
    config['server_name'] = request.form.get('server_name') or "Your Media Server"
    
    selected = request.form.get('theme', 'dark')
    if selected in ['dark', 'light', 'jellybrry']:
        config['theme'] = selected

    config['require_login_for_library'] = 'require_login_for_library' in request.form
    config['bypass_local'] = 'bypass_local' in request.form

    new_password_input = request.form.get('admin_password')
    if new_password_input and new_password_input.strip():
        config['admin_password'] = generate_password_hash(new_password_input)
    
    save_config(config)
    flash("Settings updated.")
    
    # ONLY refresh if core credentials changed
    if needs_refresh:
        global IS_SYNCING
        if not IS_SYNCING:
            IS_SYNCING = True
            threading.Thread(target=background_sync_worker, kwargs={"force_refresh": True, "delta_sync": False}).start()
        
    return redirect(url_for('index'))

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if load_config():
        return redirect(url_for('index'))

    if request.method == 'POST':
        step = request.form.get('step')
        if step == '1':
            url = request.form.get('jellyfin_url')
            api_key = request.form.get('api_key')
            valid, clean_url = validate_connection(url, api_key)
            if valid:
                users = get_users(clean_url, api_key)
                return render_template('setup.html', step=2, users=users, url=clean_url, api_key=api_key, theme=DEFAULT_THEME)
            else:
                flash("Could not connect to Jellyfin.")
                return render_template('setup.html', step=1)
                
        elif step == '2':
            config_data = {
                'jellyfin_url': request.form.get('url').rstrip('/'),
                'api_key': request.form.get('api_key'),
                'user_id': request.form.get('user_id'),
                'server_name': 'Your Media Server', 
                'admin_password': generate_password_hash(request.form.get('admin_password')),
                'require_login_for_library': False,
                'bypass_local': True,
                'theme': DEFAULT_THEME # Save default theme
            }
            save_config(config_data)
            fetch_library(force_refresh=True)
            session['is_admin'] = True
            return redirect(url_for('index'))

    return render_template('setup.html', step=1, theme=DEFAULT_THEME)

@app.route('/refresh')
def refresh():
    global IS_SYNCING
    if not IS_SYNCING:
        IS_SYNCING = True
        # User clicked Resync manually. Trigger Delta Sync
        threading.Thread(target=background_sync_worker, kwargs={"force_refresh": True, "delta_sync": True}).start()

    return jsonify({"status": "syncing"})

@app.route('/api/force_rebuild', methods=['POST'])
def force_rebuild():
    # Security: Only admins can trigger this when logged in
    if not session.get('is_admin'):
        return jsonify({"error": "Unauthorized"}), 403
    
    global IS_SYNCING
    if not IS_SYNCING:
        IS_SYNCING = True
        # Force rebuild
        threading.Thread(target=background_sync_worker, kwargs={"force_refresh": True, "delta_sync": False}).start()

    return jsonify({"status": "syncing"})

@app.template_filter('runtime')
def filter_runtime(ticks):
    if not ticks: return ""
    seconds = ticks // 10_000_000
    minutes = (seconds // 60) % 60
    hours = (seconds // 60) // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"

# === Boot Sequence ===

# If settings exist when container starts, trigger background sync
if load_config() and (os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug):
    boot_mode = os.environ.get('BOOTSYNC', 'full').strip().lower()

    if boot_mode == 'none':
        print("🚀 Booting up! BOOTSYNC set to 'none'. Skipping automatic sync.", flush=True)
    else:
        IS_SYNCING = True

        # Safety check: Cannot Delta sync if no cache file
        if boot_mode == 'delta' and load_cache() is not None:
            print("🚀 Booting up! BOOTSYNC set to 'delta'. Initiating fast background sync...", flush=True)
            threading.Thread(target=background_sync_worker, kwargs={"force_refresh": True, "delta_sync": True}).start()
        else:
            print("🚀 Booting up! Initiating full background sync...", flush=True)
            threading.Thread(target=background_sync_worker, kwargs={"force_refresh": True, "delta_sync": False}).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=6070, debug=True)