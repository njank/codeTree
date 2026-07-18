create or replace package customer_pkg authid current_user is

  csSystem constant varchar2(10) := 'SYSTEM';

  type TRecCustomer is record (
    nId     number(10),
    sName   varchar2(100)
  );
  type TTabCustomers is table of TRecCustomer index by binary_integer;

  cursor curActive is
    select 1 from dual;

  -- Returns the customer record for a ID.
  function getCustomer(pnId in number) return TRecCustomer;

  -- Overload: lookup by name instead of ID.
  function getCustomer(psName in varchar2) return TRecCustomer;

  procedure saveCustomer(pRecCustomer in TRecCustomer);

end customer_pkg;
/
