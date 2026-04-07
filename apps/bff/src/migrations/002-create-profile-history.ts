import { MigrationInterface, QueryRunner } from 'typeorm';

export class CreateProfileHistory1712400000002 implements MigrationInterface {
  name = 'CreateProfileHistory1712400000002';

  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS profile_history (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id TEXT NOT NULL,
        yaml_content TEXT NOT NULL,
        changed_by TEXT NOT NULL,
        changed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        comment TEXT
      );
    `);

    await queryRunner.query(`
      CREATE INDEX IF NOT EXISTS idx_profile_history_profile_id ON profile_history(profile_id);
    `);

    await queryRunner.query(`
      CREATE INDEX IF NOT EXISTS idx_profile_history_changed_at ON profile_history(changed_at DESC);
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE IF EXISTS profile_history;`);
  }
}
