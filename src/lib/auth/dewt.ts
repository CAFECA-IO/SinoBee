import { SignJWT, jwtVerify, importPKCS8, exportJWK, importJWK } from 'jose';
import type { JWTPayload, KeyObject, CryptoKey, JWK } from 'jose';
import { webAuthnRepo } from '@/repositories/webauthn.repo';
import { logger } from '@/lib/utils/logger';
import { AppError } from '@/lib/utils/error';
import { ApiCode } from '@/lib/utils/status';
import { User } from '@/generated/client';

const origin = process.env.NEXT_PUBLIC_APP_URL || 'http://localhost:3000';
const PEM_PRIVATE_KEY = process.env.DEWT_PRIVATE_KEY_PEM;

if (!PEM_PRIVATE_KEY) {
  logger.error('FATAL: DEWT_PRIVATE_KEY_PEM environment variable is not set.');
}

const DEWT_ALG = 'ES256';
const DEWT_ISSUER = origin;
const DEWT_AUDIENCE = origin;
const DEWT_EXPIRATION_TIME = process.env.DEWT_EXPIRATION_TIME ?? '24h';

interface ILoadedKeys {
  privateKey: KeyObject | CryptoKey;
  publicKey: KeyObject | CryptoKey;
}

let loadedKeys: ILoadedKeys | null = null;

async function loadKeys(): Promise<ILoadedKeys> {
  if (loadedKeys) return loadedKeys;

  if (!PEM_PRIVATE_KEY) {
    throw new AppError(
      ApiCode.INTERNAL_SERVER_ERROR,
      'Server configuration error: Missing Private Key'
    );
  }

  try {
    // Info: (20260127 - Tzuhan) Fix potential issue with .env newline escaping
    const sanitizedKey = PEM_PRIVATE_KEY.replace(/\\n/g, '\n');
    const privateKey = await importPKCS8(sanitizedKey, DEWT_ALG, { extractable: true });
    const privateJwk = await exportJWK(privateKey);
    const publicJwk: JWK = {
      kty: privateJwk.kty,
      crv: privateJwk.crv,
      x: privateJwk.x,
      y: privateJwk.y,
    };
    const publicKey = (await importJWK(publicJwk, DEWT_ALG)) as KeyObject | CryptoKey;

    loadedKeys = { privateKey, publicKey };
    return loadedKeys;
  } catch (error) {
    logger.error(`Failed to load keys: ${JSON.stringify(error)}`);
    throw new AppError(ApiCode.INTERNAL_SERVER_ERROR, 'Failed to load cryptographic keys');
  }
}

export const signDeWT = async (user: User): Promise<string> => {
  const { privateKey } = await loadKeys();

  // Info: (20251223 - Tzuhan) Payload 包含前端需要的基礎資訊
  // Info: (20260124 - Tzuhan) 加入 name 與 imageUrl，讓 JWT 具備離線呈現能力
  const payload = {
    sub: user.id,
    address: user.address,
    role: user.role,
    scope: ['user'],
    pubKeyX: user.pubKeyX,
    pubKeyY: user.pubKeyY,
    name: user.name,
    imageUrl: user.imageUrl,
  };

  const dewt = await new SignJWT(payload)
    .setProtectedHeader({ alg: DEWT_ALG })
    .setIssuer(DEWT_ISSUER)
    .setAudience(DEWT_AUDIENCE)
    .setExpirationTime(DEWT_EXPIRATION_TIME)
    .setIssuedAt()
    .sign(privateKey);

  return dewt;
};

export const verifyDeWT = async (dewt: string): Promise<JWTPayload> => {
  const { publicKey } = await loadKeys();
  try {
    const { payload } = await jwtVerify(dewt, publicKey, {
      issuer: DEWT_ISSUER,
      audience: DEWT_AUDIENCE,
    });
    return payload;
  } catch (error) {
    logger.error(`DeWT verification failed: ${JSON.stringify(error)}`);
    throw new AppError(ApiCode.UNAUTHORIZED, 'Invalid or expired token');
  }
};

export const getIdentityFromDeWT = async (
  authHeader: string | null | undefined
): Promise<User | null> => {
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return null;
  }
  const dewt = authHeader.substring(7);
  let payload: JWTPayload;

  // Info: (20260124 - Tzuhan) 1. 先驗證 Token 有效性 (這是最重要的一步，失敗直接回傳 null)
  try {
    payload = await verifyDeWT(dewt);
  } catch (error) {
    logger.error(`Token verification failed: ${JSON.stringify(error)}`);
    return null;
  }

  // Info: (20260124 - Tzuhan) 2. 嘗試從 DB 獲取最新資料 (Online Mode)
  try {
    const userId = payload.sub;
    if (userId) {
      const user = await webAuthnRepo.findUserById(userId);
      if (user) return user;
    }
  } catch (error) {
    // Info: (20260124 - Tzuhan) 3. [核心修正] 容錯降級 (Offline Mode)
    // Info: (20260124 - Tzuhan) 如果 DB 連線失敗，不拋出錯誤，而是使用 JWT Payload 重組 User 物件
    logger.warn(
      `[Auth] DB Connection Failed. Falling back to JWT Payload. Error: ${(error as Error).message}`
    );
  }

  // Info: (20260124 - Tzuhan) 4. 重組 User 物件 (Fallback)
  // Info: (20260124 - Tzuhan) 只要簽名驗證通過 (步驟 1)，這裡的資料就是可信的
  if (!payload.sub || !payload.address) return null;
  const result = {
    id: payload.sub as string,
    address: payload.address as string,
    role: (payload.role as 'USER' | 'ADMIN') || 'USER',
    pubKeyX: payload.pubKeyX as string,
    pubKeyY: payload.pubKeyY as string,
    name: (payload.name as string) || `User ${(payload.address as string).slice(0, 6)}`,
    imageUrl: (payload.imageUrl as string) || null,
    // Info: (20260124 - Tzuhan) 以下欄位在 JWT 中不存在，給予空值或預設值
    credentialId: null,
    currentChallenge: null,
    identityAddress: null,
    createdAt: payload.iat ? new Date(payload.iat * 1000) : new Date(),
    updatedAt: payload.iat ? new Date(payload.iat * 1000) : new Date(),
  } as User;
  console.log(result);
  return result;
};
