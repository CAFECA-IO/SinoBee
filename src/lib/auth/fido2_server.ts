import { server } from '@passwordless-id/webauthn';
import type {
  RegistrationJSON,
  AuthenticationJSON,
  CredentialInfo,
} from '@passwordless-id/webauthn/dist/esm/types';
import { AppError } from '@/lib/utils/error';
import { ApiCode } from '@/lib/utils/status';

const ORIGIN = process.env.NEXT_PUBLIC_APP_URL || 'http://localhost:3000';

/**
 * Info: (20251223 - Tzuhan)
 * 驗證註冊 (Registration)
 */
export async function verifyRegistration(
  registration: RegistrationJSON,
  expectedChallenge: string
) {
  try {
    const result = await server.verifyRegistration(registration, {
      challenge: expectedChallenge,
      origin: ORIGIN,
    });
    return result;
  } catch (error) {
    console.error('Registration verification failed:', error);
    throw new AppError(ApiCode.VALIDATION_ERROR, 'Invalid registration data');
  }
}

/**
 * Info: (20251223 - Tzuhan)
 * 驗證登入 (Authentication)
 */
export async function verifyAuthentication(
  authentication: AuthenticationJSON,
  credential: CredentialInfo,
  expectedChallenge: string
) {
  try {
    // Info: (20251223 - Tzuhan) server.verifyAuthentication 預期第二個參數符合 CredentialInfo
    const result = await server.verifyAuthentication(authentication, credential, {
      challenge: expectedChallenge,
      origin: ORIGIN,
      userVerified: false,
    });
    return result;
  } catch (error) {
    console.error('Authentication verification failed:', error);
    throw new AppError(ApiCode.UNAUTHORIZED, 'Invalid signature or challenge');
  }
}
