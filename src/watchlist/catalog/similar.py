from watchlist.home.feeds import Card, interleave, is_listable, to_card
from watchlist.integrations.tmdb import TMDBClient
from watchlist.integrations.tmdb_schemas import BrowseItem, MediaType


def similar_cards(items: list[BrowseItem], media_type: MediaType, tmdb_id: int) -> list[Card]:
    return interleave(
        [
            to_card(item)
            for item in items
            if is_listable(item) and (item.media_type, item.id) != (media_type, tmdb_id)
        ]
    )


async def find_similar(client: TMDBClient, media_type: MediaType, tmdb_id: int) -> list[Card]:
    cards = similar_cards(
        await client.get_recommendations(media_type, tmdb_id), media_type, tmdb_id
    )
    if cards:
        return cards
    return similar_cards(await client.get_similar(media_type, tmdb_id), media_type, tmdb_id)
