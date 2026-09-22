# Discord Book Bot

A Discord bot for finding books and sharing what your server is reading.

## Highlights

- Searches by title, author, ISBN-10, or ISBN-13
- Shows covers, descriptions, publication details, ISBNs, ratings, tags, and external links
- Tracks Want to read, Reading, and Finished shelves per server
- Supports half-star ratings and reading activity announcements
- Shows who else in the server wants to read, is reading, or has finished a book
- Uses persistent Discord components, so book-card actions survive restarts

## Stack

Python 3.12, discord.py, httpx, aiosqlite, and Pydantic Settings.

Metadata currently comes from Hardcover through a provider-agnostic catalog layer, so additional sources can be added without changing the rest of the bot.
