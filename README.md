# Jellybrry

A lightweight, filter-focused frontend for Jellyfin media servers.
Designed to help you and your users browse your Jellyfin library with search, sorting and filtering without needing to log in.

## Features
- **No Login Required:** Guests can browse your collection instantly.
- **Advanced Filtering & Sorting:** Filter by Media Type, Jellyfin Library, Genre, and Collection. Sort by Title, Release Year, Runtime, Community Rating, Date Added, and group by Genres, Library, or Collection.
- **Rich Media Details:** Details modal with Release Year, Runtime, Community Rating, MPAA Rating, and Director. Info Tooltip with Video, Audio, Subtitle, File Size, and Path details.
- **Admin & Guest Themes:** Set a server-wide theme as admin, which all users will see initially. Guests can override admin-assigned theme for their browser only by changing the theme in Settings without logging in as Admin.
- **Robust Syncing:** Delta sync for all users to reflect newly added media and Full sync for Admins if a full library update is needed. All syncing happens in the background with a persistent status notification.
- **Lock Library Visibility Behind Password:** Admins can hide library behind admin password. For servers you don't share with guests.
- **No Login on Home Network Option:** For locked libraries, admins can stay logged in at home.
- **Mobile Optimized:** So guests can browse your library from anywhere.

## Installation: Docker (Recommended)

1. **YML:**  
   Copy this `docker-compose.yml` to get started immediately.
   ```yaml
   services:
    jellybrry:
      image: ghcr.io/eightbitmagnets/jellybrry:latest
      container_name: jellybrry
      restart: unless-stopped
      ports:
        - "6070:6070"
      volumes:
        - ./config:/config

2. **Set Up Environmental Variables:**  
   Sync Behavior on Container Startup:
   ```bash
   - BOOTSYNC=full
   - BOOTSYNC=delta
   - BOOTSYNC=none
   ```
   Full (Default): Queries Jellyfin API for entire library and rebuilds local cache.
   Delta (Quick): Queries Jellyfin API only for items that have been added or modified since the last successful sync timestamp.
   None: No sync is performed when docker container is started.
   
   UI Themes:
   ```bash
   - THEME=dark
   - THEME=light
   - THEME=jellybrry
4. **Run with Docker Compose:**
   ```bash
   docker compose up -d
5. **Open in Browser:**  
   Go to http://localhost:6070

   **Configuration**
   On first launch, the app will ask for:
   - Jellyfin Server URL
   - Jellyfin API Key
   - Admin Password (for settings)

## Screenshots

### Desktop Dashboard & Settings
![Desktop Dashboard](screenshots/desktop_jellybrry.png)
![Desktop Details](screenshots/desktop_details.png)
<p align="center">
  <img src="screenshots/desktop_guest-settings.png" height="500" />
   &nbsp;
  <img src="screenshots/desktop_admin-settings.png" height="500" />
</p>  

### Themes
<p align="center">
  <img src="screenshots/desktop_dark.png" width="33%" />
  <img src="screenshots/desktop_light.png" width="33%" />
  <img src="screenshots/desktop_jellybrry.png" width="33%" />
</p>

### Mobile
<p align="center">
  <img src="screenshots/mobile_jellybrry.png" width="24.5%" />
  <img src="screenshots/mobile_description.png" width="24.5%" />
  <img src="screenshots/mobile_filter.png" width="24.5%" />
  <img src="screenshots/mobile_sort.png" width="24.5%" />
</p>

**License:** GNU General Public License v3.0
