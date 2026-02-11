import { createWalletClient, http } from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { isuncoin } from '@/lib/viem_public';
import 'dotenv';


export * from '@/lib/viem_public';

// Info: (20260121 - Tzuhan) --- Server Side Config ---
const hexPrivateKey = process.env.ISUNCOIN_PRIVATE_KEY as `0x${string}`;

/**
 * Info: (20260121 - Tzuhan) --- 2. 伺服器端帳戶 (Relayer) ---
 * Validate key requires 0x prefix and 64 hex characters (total length 66) or 32 bytes
 */
const isValidKey = hexPrivateKey && hexPrivateKey.startsWith('0x') && hexPrivateKey.length >= 66;

export const account = isValidKey ? privateKeyToAccount(hexPrivateKey) : null;

/**
 * Info: (20260121 - Tzuhan) 伺服器端 Wallet Client (用於 Relayer 交易)
 * 注意：不要在 Client Component 使用此變數，因為它依賴私鑰
 */
export const walletClient =
    account
        ? createWalletClient({ account, chain: isuncoin, transport: http() })
        : null;
