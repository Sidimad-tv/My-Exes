import builtins
import json
import re
from os.path import join

import requests
from bs4 import BeautifulSoup

from utils.constants.macros import ERR_MSG, USER_ERROR, APP_ERROR
from utils.main_service import main_service
from utils.structs import BaseElement, BaseService, CustomException
from utils.tools.args import check_range
from utils.tools.cdm import get_pssh_from_cenc_pssh
from utils.tools.common import get_valid_filename, clean_url, update_url_params


class france_tv(BaseService):
    DEMO_URLS = [
        "https://www.france.tv/spectacles-et-culture/festival-rock-en-seine/6428750-gossip-en-concert-a-rock-en-seine-2024.html",
        "https://www.france.tv/france-3/commissaire-dupin/commissaire-dupin-saison-1/6366821-une-famille-endeuillee.html",
        "https://www.france.tv/enfants/six-huit-ans/tortues-ninja-les-chevaliers-d-ecaille/saison-1/6137135-a-la-recherche-du-technodrome.html",
        "https://www.france.tv/films/films-drame/494223-monsieur-klein.html",
        "https://www.france.tv/films/6096218-butch-cassidy-et-le-kid.html",
        "https://www.france.tv/collection/6314801-ils-ont-vecu-les-jeux/",
        "https://www.france.tv/collection/6250718-histoires-de-famille/",
        "https://www.france.tv/france-2/un-si-grand-soleil/",
        "https://www.france.tv/enfants/six-huit-ans/il-etait-une-fois-ces-droles-d-objets/",
        "https://www.france.tv/enfants/six-huit-ans/tortues-ninja-les-chevaliers-d-ecaille/",
        "https://www.france.tv/france-3/ouija-un-ete-meurtrier/toutes-les-videos/",
    ]

    VIDEOS_URL = None
    LICENSE_URL = None
    BASE_URL = 'https://www.france.tv'
    COLLECTION_URL = "https://www.france.tv/api/collections/"

    MAIN_JS = "https://static.francetv.fr/magnetoscope/main.magnetoscope.js"
    PAGE_SIZE = 50

    @staticmethod
    def test_service():
        main_service.run_service(france_tv)

    @staticmethod
    def credentials_needed():
        return {"NONE_NEEDED": True}

    @staticmethod
    def set_endpoints():
        response = requests.get(france_tv.MAIN_JS).content.decode()
        france_tv.LICENSE_URL = "https://api-drm.ftven.fr/v1/wvls/contentlicenseservice/v1/licenses"

        france_tv.VIDEOS_URL = re.findall(r'gateway:\{url:"([^"]+)"', response)[0]
        france_tv.VIDEOS_URL += "{video_id}"

    @staticmethod
    def initialize_service():
        if france_tv.LICENSE_URL is None:
            france_tv.set_endpoints()
        return france_tv

    @staticmethod
    def get_keys(challenge, additional):
        licence = requests.post(
            france_tv.LICENSE_URL,
            headers={'nv-authorizations': additional["video_token"]},
            data=challenge
        )
        licence.raise_for_status()
        return licence.content

    @staticmethod
    def get_video_id(source_url, source_text):
        content_id = source_url.split("/")[-1].split("-")[0]

        lists = []
        for r in [
            r'[\'"]content_id[\'\\"][^:]*:([^\\"\',]+)[,\\"\']',
            r'[\'"]video_factory_id[\'\\"][^:]*:[\\"\']*([^\\"\',]+)[\\"\']'
        ]:
            temp_ids = re.findall(r, source_text, flags=re.DOTALL)
            current_ids = []
            for i in temp_ids:
                if i not in current_ids:
                    current_ids.append(i)

            lists.append(current_ids)

        size = min(len(lists[0]), len(lists[1]))
        for i in range(size):
            if content_id == lists[0][i]:
                return lists[1][i]
        return None

    @staticmethod
    def get_video_data(source_element):
        if source_element.additional is None:
            source_element.additional = {}
        video_id = source_element.additional.get("video_id", None)

        if video_id is None:
            source_text = requests.get(source_element.url).content.decode()
            video_id = france_tv.get_video_id(source_element.url, source_text)

        if video_id is None:
            raise CustomException(ERR_MSG.format(
                type=f'{USER_ERROR}',
                url=source_element.url,
                reason="The content wasn't found / is a text article, or isn't available yet",
                solution="Do not attempt to download it or wait until you can play it on your browser"
            ))

        response = requests.get(
            france_tv.VIDEOS_URL.format(video_id=video_id),
            params={'domain': 'domain', 'browser': 'browser', 'capabilities': 'drm'}
        )
        if response.status_code == 404:
            raise CustomException(ERR_MSG.format(
                type=f'{USER_ERROR}',
                url=source_element.url,
                reason="The content isn't available",
                solution="Do not attempt to download it"
            ))

        response = response.content.decode()
        response = json.loads(response)

        if source_element.element is None:
            meta = response.get("meta", None)
            if meta is None:
                meta = {}

            video_title = meta.get("title", None)
            if video_title not in [None, ""]:
                for f in ["pre_title", "additional_title"]:
                    if meta.get(f, None) not in ["", None]:
                        video_title += " " + meta[f]

            if video_title is None:
                video_title = ""
            video_title = video_title.strip()

            if len(video_title) == 0:
                video_title = source_element.url.split("/")[-1].split(".html")[0]
            source_element.element = get_valid_filename(video_title)

        if source_element.collection is None:
            source_element.collection = join(
                str(builtins.CONFIG["DOWNLOAD_COMMANDS"]["OUTPUT_MEDIA_PATH"]),
                france_tv.__name__
            )

        video_info = response["video"]
        is_drm = video_info.get("drm", False) is True
        token_dict = video_info["token"]
        if is_drm:
            token_url = token_dict["drm"]
        else:
            token_url = token_dict["akamai"]

        manifest = video_info["url"]
        additional = {}
        pssh = None
        if not is_drm:
            manifest = json.loads(requests.get(
                update_url_params(token_url, {"url": manifest})
            ).content.decode())["url"]
        else:
            manifest_content = requests.get(manifest)
            if manifest_content.status_code == 403:
                raise CustomException(ERR_MSG.format(
                    type=f'{USER_ERROR}',
                    url=source_element.url,
                    reason="Need French IP to access content (and in this case, even to download it) or your VPN was detected",
                    solution="Use a VPN or get a better one"
                ))
            manifest_content = manifest_content.content.decode()

            try:
                pssh = get_pssh_from_cenc_pssh(manifest_content)
            except:
                pssh = None

            if pssh is None:
                raise CustomException(ERR_MSG.format(
                    type=APP_ERROR,
                    url=source_element.url,
                    reason=f"Manifest format not supported: {manifest}. Can't extract pssh",
                    solution=f"Extend the {france_tv.__name__} service"
                ))

            response = requests.post(
                token_url,
                json={
                    "id": video_id, "drm_type": "widevine",
                    "license_type": "online"
                }
            )

            response = response.content.decode()
            response = json.loads(response)
            additional["video_token"] = response["token"]

        return manifest, pssh, additional

    @staticmethod
    def get_collection_elements(collection_url):
        collection_url = clean_url(collection_url)
        content_id = collection_url.split("/")[-1].split("-")[0]
        is_id_number = re.match(r"\d+$", content_id) is not None

        if collection_url.endswith(".html"):
            if is_id_number:
                return [BaseElement(url=collection_url)]
            if collection_url.endswith("direct.html"):
                return None

        response = requests.get(collection_url)
        response = response.content.decode()
        soup = BeautifulSoup(response, 'html5lib')

        try:
            collection_title = soup.title.text
            collection_title = collection_title.replace("- Les épisodes en replay - France TV", "")
            collection_title = collection_title.replace("- Toutes les vidéos en streaming - France TV", "")
            assert len(collection_title) > 0
        except:
            collection_title = collection_url.split(france_tv.BASE_URL)[-1]
        collection_title = join(
            join(
                str(builtins.CONFIG["DOWNLOAD_COMMANDS"]["OUTPUT_MEDIA_PATH"]),
                france_tv.__name__
            ),
            get_valid_filename(collection_title)
        )

        collection = []
        collection_url += "/"
        if ("/collection/" in collection_url and is_id_number) or collection_url.endswith("/toutes-les-videos/"):
            page = -1
            video_index = 0
            param_name = "id"
            param_value = content_id

            if not is_id_number:
                param_name = "slug"
                param_value = collection_url.split(france_tv.BASE_URL + "/")[1]
                param_value = param_value.split("/toutes-les-videos")[0].replace("/", "_")

            while True:
                page += 1
                page_response = requests.get(
                    france_tv.COLLECTION_URL,
                    params={param_name: param_value, "type": "collection", "page": page}
                )
                page_response = json.loads(page_response.content.decode())

                try:
                    contents = page_response["result"]
                    assert len(contents) > 0
                except:
                    contents = []

                if len(contents) == 0:
                    break

                for video_info in contents:
                    video_index += 1
                    check = check_range(False, None, video_index)
                    if check is True:
                        continue
                    elif check is False:
                        return collection

                    video_url = video_info["content"]["url"]
                    if not video_url.startswith(france_tv.BASE_URL):
                        video_url = f'{france_tv.BASE_URL}{video_url}'

                    video_title = f'Video_{video_index}'
                    video_title += " " + video_info["content"].get(
                        "title",
                        video_url.split("/")[-1].split(".html")[0]
                    )

                    if not video_url.endswith(".html"):
                        continue
                    if video_info["content"].get("type", "").lower() in ["article"]:
                        continue
                    label = video_info["content"].get("label", None)
                    if label is not None and type(label) is str:
                        if label.lower() in ["indisponible"]:
                            continue

                    collection.append(BaseElement(
                        url=video_url,
                        collection=collection_title,
                        element=get_valid_filename(video_title),
                        additional={"video_id": video_info.get("tracking", {}).get("video_factory_id", None)}
                    ))

            return collection

        page_seasons = soup.find_all(
            'a',
            attrs={
                'href': lambda x: x and
                                  ("/saison-" in x or "-saison-" in x) and
                                  ".html" not in x and not x.endswith("/")
            }
        )

        visited = []
        seasons = []
        for season in page_seasons:
            season_url = season["href"]
            try:
                season_index = re.search(r'saison-(\d+)', season_url).group(1)
                assert season_index.isdigit()
                season_index = int(season_index)
            except:
                continue

            if season_index in visited:
                continue
            visited.append(season_index)

            season_name = season.getText()
            seasons.append((season_url, season_index, season_name))

        seasons = sorted(seasons, key=lambda s: s[1])
        for season_url, season_index, season_name in seasons:
            check = check_range(True, season_index, None)
            if check is True:
                continue
            elif check is False:
                return collection

            if season_url[0] == "/":
                season_url = season_url[1:]
            if season_url[-1] == "/":
                season_url = season_url[:-1]

            season_name = join(collection_title, get_valid_filename(f'S{season_index} {season_name}'))
            page = -1
            slug_value = season_url.replace("/", "_")

            episodes = []
            extras = []
            stop_flags = []
            page_filters = ["only-replay", "only-extract"]

            while len(stop_flags) != len(page_filters):
                page += 1
                for page_filter in page_filters:
                    if page_filter in stop_flags:
                        continue

                    season_response = requests.get(
                        france_tv.COLLECTION_URL,
                        params={
                            "slug": slug_value, "type": "season", "page": page,
                            "size": france_tv.PAGE_SIZE, "filter": page_filter
                        }
                    )
                    season_response = json.loads(season_response.content.decode())

                    try:
                        contents = season_response["result"]
                        assert len(contents) > 0
                    except:
                        contents = []

                    if len(contents) == 0:
                        stop_flags.append(page_filter)
                        continue

                    for video_info in contents:
                        video_url = video_info["content"]["url"]
                        if not video_url.startswith(france_tv.BASE_URL):
                            video_url = f'{france_tv.BASE_URL}{video_url}'

                        if not video_url.endswith(".html"):
                            continue
                        if video_info["content"].get("type", "").lower() in ["article"]:
                            continue
                        label = video_info["content"].get("label", None)
                        if label is not None and type(label) is str:
                            if label.lower() in ["indisponible"]:
                                continue

                        try:
                            assert page_filter == "only-replay"
                            video_index = video_info["content"]["title"].lower()
                            video_index = re.search(r's\d+\s*e(\d+)', video_index).group(1)
                            assert video_index.isdigit()
                            video_index = int(video_index)
                        except:
                            video_index = None

                        video_title = video_info["content"].get(
                            "title",
                            video_url.split("/")[-1].split(".html")[0]
                        )

                        if video_index is not None:
                            episodes.append((video_url, video_index, video_title, video_info))
                        else:
                            extras.append((video_url, video_index, video_title, video_info))

            episodes = sorted(episodes, key=lambda e: e[1])
            try:
                last_index = episodes[-1][1] + 1
            except:
                last_index = 1
            episodes += extras

            for episode_url, episode_index, episode_name, episode_info in episodes:
                if episode_index is None:
                    episode_index = last_index
                    last_index += 1

                check = check_range(False, season_index, episode_index)
                if check is True:
                    continue
                elif check is False:
                    return collection

                episode_name = f'E{episode_index} {episode_name}'
                collection.append(BaseElement(
                    url=episode_url,
                    collection=season_name,
                    element=get_valid_filename(episode_name),
                    additional={"video_id": episode_info.get("tracking", {}).get("video_factory_id", None)}
                ))

        return collection
