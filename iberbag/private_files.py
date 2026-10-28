"""Restrict backup folders with native Windows DACLs or POSIX permissions."""
import os
from pathlib import Path


def windows_acl(path, create):
    """Use protected OS APIs, not shells, inherited modules or external utilities."""
    import ctypes as c
    from ctypes import wintypes as w
    api=c.WinDLL('advapi32',use_last_error=True)
    kernel=c.WinDLL('kernel32',use_last_error=True)
    pointer=c.c_void_p
    kernel.GetCurrentProcess.argtypes=[];kernel.GetCurrentProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle.restype=w.BOOL
    kernel.LocalFree.argtypes=[pointer];kernel.LocalFree.restype=pointer
    api.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)];api.OpenProcessToken.restype=w.BOOL
    api.GetTokenInformation.argtypes=[w.HANDLE,c.c_int,pointer,w.DWORD,c.POINTER(w.DWORD)];api.GetTokenInformation.restype=w.BOOL
    api.ConvertSidToStringSidW.argtypes=[pointer,c.POINTER(pointer)];api.ConvertSidToStringSidW.restype=w.BOOL
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,c.POINTER(pointer),c.POINTER(w.DWORD)]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype=w.BOOL
    api.SetFileSecurityW.argtypes=[w.LPCWSTR,w.DWORD,pointer];api.SetFileSecurityW.restype=w.BOOL
    api.GetFileSecurityW.argtypes=[w.LPCWSTR,w.DWORD,pointer,w.DWORD,c.POINTER(w.DWORD)];api.GetFileSecurityW.restype=w.BOOL
    api.GetSecurityDescriptorControl.argtypes=[pointer,c.POINTER(w.WORD),c.POINTER(w.DWORD)];api.GetSecurityDescriptorControl.restype=w.BOOL
    api.ConvertStringSidToSidW.argtypes=[w.LPCWSTR,c.POINTER(pointer)];api.ConvertStringSidToSidW.restype=w.BOOL
    api.GetSecurityDescriptorDacl.argtypes=[pointer,c.POINTER(w.BOOL),c.POINTER(pointer),c.POINTER(w.BOOL)];api.GetSecurityDescriptorDacl.restype=w.BOOL
    api.GetAclInformation.argtypes=[pointer,pointer,w.DWORD,c.c_int];api.GetAclInformation.restype=w.BOOL
    api.GetAce.argtypes=[pointer,w.DWORD,c.POINTER(pointer)];api.GetAce.restype=w.BOOL
    api.EqualSid.argtypes=[pointer,pointer];api.EqualSid.restype=w.BOOL

    def check(result):
        if not result:
            # Error codes only: never expose usernames, SIDs, paths or credentials.
            raise ValueError('ACL Windows privada não confirmada (Win32 '+str(c.get_last_error())+'). Nenhum backup/restauro iniciado.')

    token=w.HANDLE();check(api.OpenProcessToken(kernel.GetCurrentProcess(),8,c.byref(token)))
    try:
        size=w.DWORD()
        api.GetTokenInformation(token,1,None,0,c.byref(size))
        if not size.value:check(False)
        user=c.create_string_buffer(size.value)
        check(api.GetTokenInformation(token,1,user,size,c.byref(size)))
        # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first member is PSID.
        sid_pointer=c.cast(user,c.POINTER(pointer))[0]
        sid_string=pointer();check(api.ConvertSidToStringSidW(sid_pointer,c.byref(sid_string)))
        try:sid=c.wstring_at(sid_string)
        finally:kernel.LocalFree(sid_string)
    finally:kernel.CloseHandle(token)

    if create:
        descriptor=pointer()
        check(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f'D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)',1,c.byref(descriptor),None))
        try:
            # DACL + PROTECTED_DACL: never change owner, group or SACL.
            check(api.SetFileSecurityW(str(path),0x80000004,descriptor))
        finally:kernel.LocalFree(descriptor)

    size=w.DWORD()
    api.GetFileSecurityW(str(path),4,None,0,c.byref(size))
    if not size.value:check(False)
    descriptor=c.create_string_buffer(size.value)
    check(api.GetFileSecurityW(str(path),4,descriptor,size,c.byref(size)))
    control=w.WORD();revision=w.DWORD()
    check(api.GetSecurityDescriptorControl(descriptor,c.byref(control),c.byref(revision)))
    if not control.value & 0x1000:raise ValueError('ACL Windows privada: herança não protegida.')
    # Compare binary SIDs. SDDL aliases (e.g. local Administrator) are not
    # stable textual identities and must not be mistaken for additional users.
    present=w.BOOL();defaulted=w.BOOL();dacl=pointer()
    check(api.GetSecurityDescriptorDacl(descriptor,c.byref(present),c.byref(dacl),c.byref(defaulted)))
    if not present.value or not dacl.value:raise ValueError('ACL Windows privada: DACL obrigatória em falta.')
    class AclSize(c.Structure):
        _fields_=[('count',w.DWORD),('used',w.DWORD),('free',w.DWORD)]
    info=AclSize();check(api.GetAclInformation(dacl,c.byref(info),c.sizeof(info),2))
    if not 1<=info.count<=2:raise ValueError('ACL Windows privada: número de regras inesperado.')
    system=pointer();check(api.ConvertStringSidToSidW('S-1-5-18',c.byref(system)))
    try:
        found_user=found_system=False
        for i in range(info.count):
            ace=pointer();check(api.GetAce(dacl,i,c.byref(ace)))
            header=c.cast(ace,c.POINTER(c.c_ubyte))
            mask=c.cast(ace.value+4,c.POINTER(w.DWORD))[0]
            if header[0]!=0 or header[1]!=3 or mask!=0x1f01ff:
                raise ValueError('ACL Windows privada: regra inesperada.')
            identity=pointer(ace.value+8)  # ACCESS_ALLOWED_ACE.SidStart
            is_user=bool(api.EqualSid(identity,sid_pointer));is_system=bool(api.EqualSid(identity,system))
            if not is_user and not is_system:raise ValueError('ACL Windows privada: acesso de terceiros recusado.')
            found_user|=is_user;found_system|=is_system
        if not found_user or not found_system:raise ValueError('ACL Windows privada: permissões obrigatórias em falta.')
    finally:kernel.LocalFree(system)


def private_folder(folder,create=False):
    path=Path(folder).absolute()
    if path.resolve()!=path:raise ValueError('Pasta privada não pode usar symlinks/junctions.')
    if create:path.mkdir(parents=True,exist_ok=False,mode=0o700)
    if os.name=='nt':
        # Reparse attributes include junctions and cloud placeholders.
        if path.stat().st_file_attributes & 0x400:raise ValueError('Pasta privada não pode ser reparse point.')
        windows_acl(path,create)
    elif path.stat().st_uid!=os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError('Pasta privada deve pertencer ao utilizador e ter permissões 0700.')
    return path


def owned_file(folder,name):
    path=Path(folder)/name
    if path.is_symlink() or path.resolve()!=path.absolute():raise ValueError('Ficheiro de backup não pode ser ligação externa.')
    return path
