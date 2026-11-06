from typing import Any


def episode(number: int, air_date: str = "2020-01-01", name: str | None = None) -> dict[str, Any]:
    return {
        "episode_number": number,
        "name": name if name is not None else f"Епізод {number}",
        "air_date": air_date,
        "runtime": 45,
        "overview": "",
    }


def season(number: int, episodes: int = 3, air_date: str = "2020-01-01") -> dict[str, Any]:
    return {
        "season_number": number,
        "name": f"Сезон {number}",
        "air_date": air_date,
        "episode_count": episodes,
        "episodes": [episode(index, air_date) for index in range(1, episodes + 1)],
    }


def series(
    tmdb_id: int = 1399, seasons: int = 2, overview: str = "Опис серіалу", specials: bool = True
) -> dict[str, Any]:
    summaries = [
        {"season_number": n, "name": f"Сезон {n}", "air_date": "2020-01-01", "episode_count": 3}
        for n in range(0 if specials else 1, seasons + 1)
    ]
    return {
        "id": tmdb_id,
        "name": "Гра престолів",
        "original_name": "Game of Thrones",
        "overview": overview,
        "poster_path": "/poster.jpg",
        "first_air_date": "2011-04-17",
        "status": "Ended",
        "genres": [{"id": 18, "name": "Драма"}],
        "seasons": summaries,
    }


def series_with_seasons(tmdb_id: int, numbers: list[int], episodes: int = 3) -> dict[str, Any]:
    payload = series(tmdb_id)
    for number in numbers:
        payload[f"season/{number}"] = season(number, episodes)
    return payload


def movie(tmdb_id: int = 27205, overview: str = "Опис фільму") -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "title": "Початок",
        "original_title": "Inception",
        "overview": overview,
        "poster_path": "/inception.jpg",
        "release_date": "2010-07-15",
        "status": "Released",
        "runtime": 148,
        "genres": [{"id": 878, "name": "Фантастика"}],
    }


def search_page(*results: dict[str, Any]) -> dict[str, Any]:
    return {"page": 1, "total_pages": 1, "total_results": len(results), "results": list(results)}


def search_tv(tmdb_id: int = 1399, name: str = "Гра престолів") -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "media_type": "tv",
        "name": name,
        "original_name": "Game of Thrones",
        "first_air_date": "2011-04-17",
        "poster_path": "/poster.jpg",
        "overview": "",
    }


def search_movie(tmdb_id: int = 27205) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "media_type": "movie",
        "title": "Початок",
        "original_title": "Inception",
        "release_date": "",
        "poster_path": None,
        "overview": "",
    }


def search_person() -> dict[str, Any]:
    return {"id": 1, "media_type": "person", "name": "Актор"}
