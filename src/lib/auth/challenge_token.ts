import { SignJWT, jwtVerify } from 'jose';
import { randomBytes } from 'crypto';
import { logger } from '@/lib/utils/logger';

const SECRET = new TextEncoder().encode(process.env.DEWT_PRIVATE_KEY_PEM || 'temporary_secret');

export async function generateChallengeToken() {
  const challenge = randomBytes(32).toString('base64url');

  // Info: (20260105 - Tzuhan) 簽發一個短效期的 JWT，裡面包含 challenge
  const token = await new SignJWT({ challenge })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setExpirationTime('5m') // Info: (20260105 - Tzuhan) 5分鐘有效
    .sign(SECRET);

  return { challenge, token };
}

export async function verifyChallengeToken(token: string): Promise<string> {
  try {
    const { payload } = await jwtVerify(token, SECRET);
    return payload.challenge as string;
  } catch (e) {
    logger.error('Challenge token verification failed:', { message: (e as Error).message });
    throw new Error('Invalid or expired login session');
  }
}
