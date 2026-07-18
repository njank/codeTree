create or replace package body customer_pkg is

  gnCalls number := 0;

  -- Returns the customer record for a ID.
  function getCustomer(pnId in number) return TRecCustomer is
    vRecCustomer TRecCustomer;
    -- getCustomer appears in this comment and in the 'getCustomer' string
    vsNote varchar2(100) := 'call getCustomer here';
  begin
    gnCalls := gnCalls + 1;
    vRecCustomer.nId := pnId;
    vRecCustomer.sName := 'NAME';
    --logAccess(pnId);
    return vRecCustomer;
  end getCustomer;

  function getCustomer(psName in varchar2) return TRecCustomer is
    vRecCustomer TRecCustomer;
  begin
    vRecCustomer.sName := upper(psName);
    return vRecCustomer;
  end getCustomer;

  procedure saveCustomer(pRecCustomer in TRecCustomer) is

    procedure validate(pnId in number) is
      vbOk boolean := true;
    begin
      if pnId is null then
        raise_application_error(-20000, 'no id');
      end if;
    end validate;

  begin
    validate(pRecCustomer.nId);
    for i in 1 .. 3 loop
      gnCalls := gnCalls + 1;
    end loop;
  end saveCustomer;

end customer_pkg;
/
