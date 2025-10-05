"""Support to interface with the Roon API."""

import logging

from homeassistant.components.media_player import BrowseError, BrowseMedia, MediaClass


class UnknownMediaType(BrowseError):
    """Unknown media type."""


EXCLUDE_ITEMS = {
    "Play Album",
    "Play Artist",
    "Play Playlist",
    "Play Composer",
    "Play Now",
    "Play From Here",
    "Queue",
    "Start Radio",
    "Add Next",
    "Play Radio",
    "Play Work",
    "Settings",
    "Search",
    "Search Tidal",
    "Search Qobuz",
}

# Maximum number of items to pull back from the API
ITEM_LIMIT = 3000

_LOGGER = logging.getLogger(__name__)


def encode_path_component(component: str) -> str:
    """Encode a path component to handle special characters like forward slashes."""
    # Replace forward slashes with a safe encoding
    return component.replace("/", "⟨SLASH⟩")


def decode_path_component(encoded: str) -> str:
    """Decode a path component to restore original characters."""
    # Restore forward slashes from encoding
    return encoded.replace("⟨SLASH⟩", "/")


def create_path_id(path_parts: list[str]) -> str:
    """Create a path-based content ID from path components."""
    encoded_parts = [encode_path_component(part) for part in path_parts]
    return f"path:{'/'.join(encoded_parts)}"


def parse_path_id(path_content_id: str) -> list[str]:
    """Parse a path-based content ID into components."""
    if not path_content_id.startswith("path:"):
        return []
    
    path_string = path_content_id[5:]  # Remove "path:" prefix
    if not path_string:
        return []
    
    encoded_parts = path_string.split("/")
    return [decode_path_component(part) for part in encoded_parts]


def browse_media(zone_id, roon_server, media_content_type=None, media_content_id=None):
    """Implement the websocket media browsing helper."""
    try:
        _LOGGER.debug("browse_media: %s: %s", media_content_type, media_content_id)
        
        if media_content_type == "track":
            # Tracks cannot be browsed into, raise appropriate error
            raise BrowseError(f"Track items cannot be browsed: {media_content_id}")
        elif media_content_type in [None, "library"]:
            # Check if this is a path-based content ID
            if media_content_id and media_content_id.startswith("path:"):
                return path_based_browse(roon_server, zone_id, media_content_id)
            else:
                return library_payload(roon_server, zone_id, media_content_id)
        else:
            raise UnknownMediaType(f"Unsupported media type: {media_content_type}")

    except UnknownMediaType as err:
        raise BrowseError(
            f"Media not found: {media_content_type} / {media_content_id}"
        ) from err




def library_payload(roon_server, zone_id, media_content_id):
    """Create response payload for the library using path-based navigation."""
    
    # Use path-based navigation for all content IDs
    if media_content_id and media_content_id.startswith("path:"):
        return path_based_browse(roon_server, zone_id, media_content_id)
    
    # Show root browsing for None or empty content IDs
    display_title = "Roon Music Library"
    
    # Fresh root level browsing with path-based results
    opts = {
        "hierarchy": "browse",
        "zone_or_output_id": zone_id,
        "pop_all": True,
        "count": ITEM_LIMIT,
    }
    
    result_header = roon_server.roonapi.browse_browse(opts)
    _LOGGER.debug("Root browse result header: %s", result_header)
    
    if not isinstance(result_header, dict) or "list" not in result_header:
        _LOGGER.error("Invalid root browse result: %s", result_header)
        raise BrowseError("Could not access Roon library")
    
    result_detail = roon_server.roonapi.browse_load(opts)
    if not isinstance(result_detail, dict) or "items" not in result_detail:
        _LOGGER.error("Invalid root browse load result: %s", result_detail)
        raise BrowseError("Could not load Roon library")
    
    library_info = BrowseMedia(
        title=display_title,
        media_content_id="path:",
        media_content_type="library",
        media_class=MediaClass.DIRECTORY,
        can_play=False,
        can_expand=True,
        children=[],
    )
    
    items = result_detail["items"]
    
    for item in items:
        if item.get("title") in EXCLUDE_ITEMS:
            continue
        
        # Create path-based entries for root level items
        title = item.get("title", "")
        path_id = create_path_id([title])
        
        entry = BrowseMedia(
            title=title,
            media_class=MediaClass.DIRECTORY,
            media_content_id=path_id,
            media_content_type="library",
            can_play=title not in ["Settings"],  # Settings can't be played
            can_expand=True,
            thumbnail=roon_server.roonapi.get_image(item.get("image_key")) if item.get("image_key") else None,
        )
        library_info.children.append(entry)
    
    return library_info




def path_based_browse(roon_server, zone_id, path_content_id):
    """Browse using path-based navigation instead of item keys."""
    try:
        _LOGGER.debug("Path-based browse: %s", path_content_id)
        
        # Extract path from content ID: "path:Library/Artists/The Beatles"
        path_parts = parse_path_id(path_content_id)
        
        _LOGGER.debug("Navigating to path: %s", path_parts)
        
        # Navigate step by step through the path using browse APIs
        opts = {
            "hierarchy": "browse",
            "zone_or_output_id": zone_id,
            "pop_all": True,  # Start fresh
            "count": ITEM_LIMIT,
        }
        
        # Reset and get root items
        try:
            roon_server.roonapi.browse_browse(opts)
            load_result = roon_server.roonapi.browse_load(opts)
            current_items = load_result["items"]
        except Exception as err:
            _LOGGER.error("Failed to get root items for path navigation: %s", err)
            raise BrowseError("Could not access Roon library for path navigation") from err
        
        # Navigate through each part of the path
        for i, path_part in enumerate(path_parts):
            _LOGGER.debug("Looking for '%s' in current level", path_part)
            
            # Find the item matching this path part
            found_item = None
            for item in current_items:
                if item.get("title") == path_part:
                    found_item = item
                    break
            
            if not found_item:
                _LOGGER.error("Could not find '%s' in path %s", path_part, path_parts[:i+1])
                raise BrowseError(f"Path not found: {path_part}")
            
            # If this is not the last part, navigate into it
            if i < len(path_parts) - 1:
                navigate_opts = {
                    "hierarchy": "browse",
                    "zone_or_output_id": zone_id,
                    "item_key": found_item["item_key"],
                    "count": ITEM_LIMIT,
                }
                try:
                    roon_server.roonapi.browse_browse(navigate_opts)
                    load_result = roon_server.roonapi.browse_load(navigate_opts)
                    current_items = load_result["items"]
                except Exception as err:
                    _LOGGER.error("Navigation failed at path part '%s': %s", path_part, err)
                    raise BrowseError(f"Could not navigate to: {path_part}") from err
            else:
                # This is the target item - browse into it to show its contents
                target_opts = {
                    "hierarchy": "browse",
                    "zone_or_output_id": zone_id,
                    "item_key": found_item["item_key"],
                    "count": ITEM_LIMIT,
                }
                
                result_header = roon_server.roonapi.browse_browse(target_opts)
                
                if not isinstance(result_header, dict) or "list" not in result_header:
                    _LOGGER.error("Invalid browse result: %s", result_header)
                    raise BrowseError(f"Could not browse into: {path_part}")
                
                header = result_header["list"]
                if not isinstance(header, dict):
                    _LOGGER.error("Invalid list header: %s", header)
                    raise BrowseError(f"Invalid list format for: {path_part}")
                title = header.get("title", path_part)
                
                library_info = BrowseMedia(
                    title=title,
                    media_content_id=path_content_id,
                    media_content_type="library",
                    media_class=MediaClass.DIRECTORY,
                    can_play=True,
                    can_expand=True,
                    children=[],
                )
                
                # Load child items
                result_detail = roon_server.roonapi.browse_load(target_opts)
                
                if not isinstance(result_detail, dict) or "items" not in result_detail:
                    _LOGGER.error("Invalid browse load result: %s", result_detail)
                    raise BrowseError(f"Could not load content for: {path_part}")
                
                items = result_detail["items"]
                
                for item in items:
                    if item.get("title") in EXCLUDE_ITEMS:
                        continue
                    
                    # Create path-based content IDs for child items
                    child_title = item.get("title", "")
                    child_path_parts = path_parts + [child_title]
                    child_path = create_path_id(child_path_parts)
                    
                    entry = BrowseMedia(
                        title=child_title,
                        media_class=MediaClass.TRACK if item.get("hint") == "action" else MediaClass.DIRECTORY,
                        media_content_id=child_path,
                        media_content_type="track" if item.get("hint") == "action" else "library",
                        can_play=True,
                        can_expand=item.get("hint") != "action",
                        thumbnail=roon_server.roonapi.get_image(item.get("image_key")) if item.get("image_key") else None,
                    )
                    library_info.children.append(entry)
                
                return library_info
        
        # Should not reach here
        raise BrowseError("Invalid path navigation")
        
    except Exception as err:
        _LOGGER.error("Error in path-based browse: %s", err)
        raise BrowseError(f"Path browsing failed: {err}") from err
