'use client';

import { Modal } from '@/components/ui/modal';

interface DeleteConfirmModalProps {
  isOpen: boolean;
  profileName: string;
  isDeleting: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export function DeleteConfirmModal({
  isOpen,
  profileName,
  isDeleting,
  onClose,
  onConfirm,
}: DeleteConfirmModalProps) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Profile 삭제"
      variant="confirm"
      confirmLabel="삭제"
      cancelLabel="취소"
      onConfirm={onConfirm}
      confirmLoading={isDeleting}
    >
      <p>
        <strong>{profileName}</strong> Profile을 삭제하시겠습니까?
      </p>
      <p className="mt-2 text-[var(--color-neutral-500)]">
        이 작업은 되돌릴 수 없습니다. 연결된 히스토리도 함께 삭제됩니다.
      </p>
    </Modal>
  );
}
