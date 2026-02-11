import z from 'zod';
import { jsonValueSchema } from '@/validators/common';

export const updateProfileSchema = z.object({
  name: z.string().min(1).max(100).optional(),
  photo: z.string().optional(),

  // Info: (20251128 - Tzuhan) 新增 SCW 相關欄位驗證，允許透過 PATCH 更新
  blockchainAddress: z.string().startsWith('0x').length(42).optional(),
  initPublicKey: jsonValueSchema.optional(), // Info: (20251224 - Tzuhan) { x: string, y: string }
  deploymentSalt: z.string().optional(),
  newAuthenticator: z
    .object({
      credentialID: z.string(),
      credentialPublicKey: z.string(),
      counter: z
        .number()
        .or(z.string())
        .transform((val) => Number(val)),
      algorithm: z.enum(['ES256', 'RS256', 'EdDSA']).default('ES256'),
      userHandle: z.string().optional(),
      label: z.string().optional(),
    })
    .optional(),
});
