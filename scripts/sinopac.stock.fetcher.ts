
import fs from 'fs';
import path from 'path';

// Usage:
// 1. Visit https://aiinvest.sinotrade.com.tw/Stock/List/TW
// 2. Open Developer Tools (F12) -> Network tab
// 3. Refresh the page and find the request to the document or an API call
// 4. Copy the "Cookie" header value and "RequestVerificationToken" (from hidden input or header)
// 5. Run:
//    SINOPAC_COOKIE='...' VERIFICATION_TOKEN='...' npx tsx scripts/sinopac.stock.fetcher.ts
//
// Note: The API is protected and requires a valid session.
// Note: The site may be under maintenance (e.g., 17:00-18:30 TPE), during which the API returns HTML.

// Define the interface for the API response item
interface StockItem {
  Code: string; // e.g., "2330"
  Name: string; // e.g., "台積電"
  ClosePrice?: number;
  DealPrice?: number;
  Yield?: number;
  ROI?: number; // Return On Investment
  // Add other properties that might be present in the API response
  [key: string]: any;
}

// Define the categories to fetch
const CATEGORIES: string[] = [
  '熱門台股',
  '高殖利率',
  '半導體高息',
  '豐投組'
];

const API_URL = 'https://aiinvest.sinotrade.com.tw/Stock/List/GetList';
const OUTPUT_DIR = path.join(process.cwd(), 'public', 'data');
const OUTPUT_FILE = path.join(OUTPUT_DIR, 'sinopac_stocks.json');

async function fetchStocks(filterName: string): Promise<StockItem[]> {
  console.log(`Fetching category: ${filterName}...`);
  try {
    const response = await fetch(API_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Cookie': process.env.SINOPAC_COOKIE || '',
        'RequestVerificationToken': process.env.VERIFICATION_TOKEN || ''
      },
      body: JSON.stringify({
        FilterName: filterName,
        IsSortDesc: true,
        SortColumn: 'Rank'
      })
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data: any = await response.json();
    // The API might return an array directly or wrapped in an object.
    // Based on common practices, let's assume it returns an array.
    // If it returns { success: true, data: [...] }, we'd need to adjust.
    // Given the browser agent findings, it likely returns a JSON list directly.
    if (Array.isArray(data)) {
      return data as StockItem[];
    } else if (data && Array.isArray(data.Data)) { // Common pattern
      return data.Data as StockItem[];
    } else {
      console.warn('Unexpected API response structure:', data);
      return [];
    }
  } catch (error) {
    console.error(`Error fetching category ${filterName}:`, error);
    return [];
  }
}

async function main() {
  const allStocks = new Map<string, any>();

  // Ensure output directory exists
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  for (const category of CATEGORIES) {
    const stocks = await fetchStocks(category);
    console.log(`Found ${stocks.length} stocks in ${category}`);

    for (const stock of stocks) {
      if (!stock.Code) continue;

      if (!allStocks.has(stock.Code)) {
        allStocks.set(stock.Code, {
          ...stock,
          _categories: [category]
        });
      } else {
        // Add category to existing stock entry
        const existing = allStocks.get(stock.Code);
        if (!existing._categories.includes(category)) {
          existing._categories.push(category);
        }
      }
    }

    // Polite delay
    await new Promise(resolve => setTimeout(resolve, 500));
  }

  const stockList = Array.from(allStocks.values()).map(s => ({
    code: s.Code,
    name: s.Name,
    price: s.ClosePrice || s.DealPrice || 0,
    yield: s.Yield || 0,
    categories: s._categories,
    raw: s // Keep raw data for debugging/completeness
  }));

  console.log(`Total unique stocks found: ${stockList.length}`);

  fs.writeFileSync(OUTPUT_FILE, JSON.stringify(stockList, null, 2));
  console.log(`Saved stock list to ${OUTPUT_FILE}`);
}

main().catch(console.error);
