import requests
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, urlparse

def search_duckduckgo(query, max_results=3):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    }
    url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        # Find result divs
        result_divs = soup.find_all("div", class_="result")
        for div in result_divs[:max_results]:
            title_a = div.find("a", class_="result__a")
            snippet_a = div.find("a", class_="result__snippet")
            if not title_a:
                continue
            title = title_a.text.strip()
            link = title_a["href"]
            snippet = snippet_a.text.strip() if snippet_a else ""
            
            if "uddg=" in link:
                parsed = urlparse(link)
                qs = parse_qs(parsed.query)
                if "uddg" in qs:
                    link = qs["uddg"][0]
            results.append({"title": title, "url": link, "snippet": snippet})
        return results
    except Exception as e:
        print("Search error:", e)
        return []

if __name__ == "__main__":
    import sys
    query = "Euro 2024 winner" if len(sys.argv) < 2 else sys.argv[1]
    print(f"Searching DuckDuckGo for: {query}...")
    results = search_duckduckgo(query)
    for r in results:
        print(f"Title: {r['title']}")
        print(f"URL: {r['url']}")
        print(f"Snippet: {r['snippet']}")
        print("-" * 20)
