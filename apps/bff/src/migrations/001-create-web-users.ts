import { MigrationInterface, QueryRunner } from 'typeorm';

export class CreateWebUsers1712400000001 implements MigrationInterface {
  name = 'CreateWebUsers1712400000001';

  public async up(queryRunner: QueryRunner): Promise<void> {
    // UserRole enum 타입 (이미 존재할 수 있으므로 IF NOT EXISTS)
    await queryRunner.query(`
      DO $$ BEGIN
        CREATE TYPE user_role_enum AS ENUM ('VIEWER', 'EDITOR', 'REVIEWER', 'APPROVER', 'ADMIN');
      EXCEPTION
        WHEN duplicate_object THEN null;
      END $$;
    `);

    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS web_users (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email VARCHAR(255) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        display_name VARCHAR(255) NOT NULL,
        role user_role_enum NOT NULL DEFAULT 'VIEWER',
        security_level_max VARCHAR(50) NOT NULL DEFAULT 'PUBLIC',
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
      );
    `);

    await queryRunner.query(`
      CREATE INDEX IF NOT EXISTS idx_web_users_email ON web_users(email);
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE IF EXISTS web_users;`);
    await queryRunner.query(`DROP TYPE IF EXISTS user_role_enum;`);
  }
}
