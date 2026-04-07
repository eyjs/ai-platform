import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
} from 'typeorm';

@Entity('profile_history')
export class ProfileHistory {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ name: 'profile_id', type: 'text' })
  profileId: string;

  @Column({ name: 'yaml_content', type: 'text' })
  yamlContent: string;

  @Column({ name: 'changed_by', type: 'text' })
  changedBy: string;

  @CreateDateColumn({ name: 'changed_at' })
  changedAt: Date;

  @Column({ type: 'text', nullable: true })
  comment: string | null;
}
