import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  UpdateDateColumn,
} from 'typeorm';

export enum UserRole {
  VIEWER = 'VIEWER',
  EDITOR = 'EDITOR',
  REVIEWER = 'REVIEWER',
  APPROVER = 'APPROVER',
  ADMIN = 'ADMIN',
}

@Entity('web_users')
export class WebUser {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ unique: true })
  email: string;

  @Column({ name: 'password_hash' })
  passwordHash: string;

  @Column({ name: 'display_name' })
  displayName: string;

  @Column({ type: 'enum', enum: UserRole, default: UserRole.VIEWER })
  role: UserRole;

  @Column({
    name: 'security_level_max',
    default: 'PUBLIC',
  })
  securityLevelMax: string;

  @Column({ name: 'is_active', default: true })
  isActive: boolean;

  @CreateDateColumn({ name: 'created_at' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at' })
  updatedAt: Date;
}
