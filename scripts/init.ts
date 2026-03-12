import * as fs from 'fs';
import * as path from 'path';

const dbDirPath = path.join(__dirname, '..', 'database');

if (!fs.existsSync(dbDirPath)) {
  console.log(`Creating database directory at: ${dbDirPath}`);
  fs.mkdirSync(dbDirPath, { recursive: true });
} else {
  console.log(`Database directory already exists at: ${dbDirPath}`);
}
