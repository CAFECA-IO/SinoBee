import { createPublicClient, http, defineChain } from 'viem';

// Info: (20260122 - Tzuhan) Prioritize settings from .env
const RPC_URL = process.env.NEXT_PUBLIC_RPC_URL || 'https://mainnet.isuncoin.com';
const CHAIN_ID = parseInt(process.env.NEXT_PUBLIC_ISUNCOIN_CHAIN_ID || '8017');

export const TAIWAN_COUNTRY_CODE = 158;
export const NTD_TOKEN_ADDRESS = process.env.NEXT_PUBLIC_NTD_TOKEN_ADDRESS as `0x${string}`;
export const KYC_TOPIC_ID = BigInt(101);
export const KYB_TOPIC_ID = BigInt(102);

// Info: (20260121 - Tzuhan) --- 1. 定義 iSunCoin 鏈 ---
export const isuncoin = defineChain({
  id: CHAIN_ID,
  name: 'iSunCoin Mainnet',
  network: 'isuncoin',
  nativeCurrency: { decimals: 18, name: 'iSunCoin', symbol: 'ISC' },
  rpcUrls: { default: { http: [RPC_URL] }, public: { http: [RPC_URL] } },
});

// Info: (20260121 - Tzuhan) --- 2. 公開客戶端 (唯讀操作) ---
// Info: (20260121 - Tzuhan) 全域單例，避免重複連線
export const publicClient = createPublicClient({ chain: isuncoin, transport: http() });
