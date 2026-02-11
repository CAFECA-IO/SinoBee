
import fs from 'fs';
import path from 'path';
import dotenv from 'dotenv';

// Info: (20260118 - Luphia) Check if all keys in .env.example exist in process.env or .env file
export function validateEnv(): boolean {
  try {
    const examplePath = path.join(process.cwd(), '.env.example');
    const envPath = path.join(process.cwd(), '.env');

    // Info: (20260118 - Luphia) 1. Check if .env exists
    if (!fs.existsSync(envPath)) {
      return false;
    }

    // Info: (20260118 - Luphia) 2. Read .env.example to get required keys
    const exampleContent = fs.readFileSync(examplePath, 'utf8');
    const exampleConfig = dotenv.parse(exampleContent);
    const requiredKeys = Object.keys(exampleConfig);

    // Info: (20260118 - Luphia) 3. Read .env to get actual keys
    const envContent = fs.readFileSync(envPath, 'utf8');
    const envConfig = dotenv.parse(envContent);

    for (const key of requiredKeys) {
      if (!(key in envConfig)) {
        console.warn(`[EnvValidator] Missing key: ${key}`);
        return false;
      }
    }

    return true;
  } catch (error) {
    console.error('[EnvValidator] Error validating env:', error);
    return false;
  }
}
